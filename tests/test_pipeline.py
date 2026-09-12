"""Offline tests. No network, no API key. These are the clinical safety net.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pgxbridge import registry, rules, safety
from pgxbridge.extract import PatternExtractor, LlmExtractor, reconcile, iter_text_blocks
from pgxbridge.models import SourceRef
from pgxbridge.phenotype import normalise

SRC = SourceRef("hospital", "r-1", "discharge-summary", "Test letter", "results")
EX = PatternExtractor()


def pheno(text: str):
    out = normalise(EX.extract(text, SRC, "SIM-000001"))
    return out[0] if out else None


class TestExtraction(unittest.TestCase):
    def test_diplotype_forms(self):
        for text, expect in [
            ("CYP2C19 *2/*2 poor metaboliser.", "poor_metaboliser"),
            ("CYP2C19 genotype *1/*2.", "intermediate_metaboliser"),
            ("CYP2C19 *1/*1.", "normal_metaboliser"),
            ("CYP2C19 *1/*17.", "rapid_metaboliser"),
            ("CYP2C19 *17/*17.", "ultrarapid_metaboliser"),
            ("CYP2C19 *2/*17.", "likely_intermediate_metaboliser"),
        ]:
            with self.subTest(text=text):
                p = pheno(text)
                self.assertIsNotNone(p, text)
                self.assertEqual(p.phenotype, expect)

    def test_spacing_and_case_variants(self):
        for text in ["cyp 2c19 *2/*2", "CYP-2C19 *2/*2", "CYP2C19  *2 / *2"]:
            with self.subTest(text=text):
                self.assertEqual(pheno(text).phenotype, "poor_metaboliser")

    def test_variant_without_gene_name(self):
        """Genomics reports often quote only the rsID."""
        p = pheno("rs4244285 homozygous for the variant allele; clopidogrel activation impaired.")
        self.assertEqual(p.gene, "CYP2C19")
        self.assertEqual(p.phenotype, "poor_metaboliser")

    def test_hgvs_without_gene_name(self):
        p = pheno("c.1905+1G>A heterozygous.")
        self.assertEqual(p.gene, "DPYD")
        self.assertEqual(p.phenotype, "intermediate_metaboliser")

    def test_carrier_positive_and_negative(self):
        self.assertEqual(pheno("HLA-B*57:01 detected.").phenotype, "positive_57_01")
        self.assertEqual(pheno("HLA-B*57:01 not detected.").phenotype, "negative")
        self.assertEqual(pheno("HLA-B*57:01 negative.").phenotype, "negative")

    def test_activity_score_banding(self):
        self.assertEqual(pheno("DPYD c.1905+1G>A homozygous.").phenotype, "poor_metaboliser")
        self.assertEqual(pheno("CYP2D6 *4/*4.").phenotype, "poor_metaboliser")
        self.assertEqual(pheno("CYP2D6 *1/*1.").phenotype, "normal_metaboliser")


class TestExtractionSafety(unittest.TestCase):
    """The false positives that would put someone else's genotype on this record."""

    def test_family_history_is_not_the_patient(self):
        for text in [
            "Her mother is a CYP2C19 poor metaboliser.",
            "Family history: father CYP2C19 *2/*2.",
            "His brother carries HLA-B*57:01.",
            "Maternal aunt is a DPYD poor metaboliser.",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(pheno(text), f"coded a relative's genotype: {text}")

    def test_pending_result_is_not_a_result(self):
        for text in [
            "CYP2C19 genotype requested; result pending.",
            "CYP2C19 sample sent to the genomics hub.",
            "TPMT result awaited.",
            "DPYD testing will be tested before cycle 1.",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(pheno(text), f"coded a pending test: {text}")

    def test_negation_is_not_cancelled_by_substring(self):
        """'not detected' contains 'detected'. It must still read as negative."""
        self.assertEqual(pheno("HLA-B*57:01 not detected.").phenotype, "negative")

    def test_no_assertion_yields_nothing(self):
        self.assertIsNone(pheno("The patient was discharged home in good condition."))
        self.assertIsNone(pheno("Consider CYP2C19 testing if clinically indicated."))


class TestPhenotypeStore(unittest.TestCase):
    def test_every_phenotype_has_a_snomed_code(self):
        for gene in registry.known_genes():
            gdef = registry.gene_def(gene)
            for key, spec in gdef["phenotypes"].items():
                with self.subTest(gene=gene, phenotype=key):
                    code = spec["snomed"]["code"]
                    self.assertRegex(code, r"^\d{6,18}$")
                    self.assertTrue(spec["snomed"]["display"])

    def test_snomed_codes_are_unique(self):
        seen = {}
        for gene in registry.known_genes():
            for key, spec in registry.gene_def(gene)["phenotypes"].items():
                code = spec["snomed"]["code"]
                self.assertNotIn(code, seen, f"{gene}:{key} reuses {code} from {seen.get(code)}")
                seen[code] = f"{gene}:{key}"

    def test_genotype_beats_a_contradictory_narrative(self):
        p = pheno("CYP2C19 *1/*1 normal metaboliser. The patient is a poor metaboliser.")
        self.assertEqual(p.phenotype, "normal_metaboliser")

    def test_derivation_is_recorded(self):
        self.assertIn("*2/*2", pheno("CYP2C19 *2/*2.").derivation)


class TestRules(unittest.TestCase):
    def test_clopidogrel_fires_for_loss_of_function_only(self):
        for key, should_fire in [("poor_metaboliser", True), ("intermediate_metaboliser", True),
                                 ("normal_metaboliser", False), ("ultrarapid_metaboliser", False)]:
            with self.subTest(phenotype=key):
                p = pheno({"poor_metaboliser": "CYP2C19 *2/*2.",
                           "intermediate_metaboliser": "CYP2C19 *1/*2.",
                           "normal_metaboliser": "CYP2C19 *1/*1.",
                           "ultrarapid_metaboliser": "CYP2C19 *17/*17."}[key])
                hits = rules.evaluate(p, "Clopidogrel 75mg tablets", "secondary prevention")
                self.assertEqual(bool(hits), should_fire)

    def test_rule_carries_its_version(self):
        hits = rules.evaluate(pheno("CYP2C19 *2/*2."), "Clopidogrel 75mg", "stroke")
        self.assertEqual(hits[0]["rule_id"], "CYP2C19-CLOPIDOGREL")
        self.assertTrue(hits[0]["rule_version"])

    def test_unrelated_drug_does_not_fire(self):
        self.assertEqual(rules.evaluate(pheno("CYP2C19 *2/*2."), "Amlodipine 5mg", ""), [])

    def test_gene_agnostic(self):
        for text, drug in [("DPYD c.1905+1G>A homozygous.", "Capecitabine 500mg"),
                           ("TPMT *3A/*3C.", "Azathioprine 50mg"),
                           ("HLA-B*57:01 detected.", "Abacavir 300mg"),
                           ("CYP2D6 *4/*4.", "Codeine 30mg")]:
            with self.subTest(drug=drug):
                self.assertTrue(rules.evaluate(pheno(text), drug, ""))

    def test_indication_selects_the_regimen(self):
        hit = rules.evaluate(pheno("CYP2C19 *2/*2."), "Clopidogrel 75mg", "stroke")[0]
        self.assertEqual(rules.choose_alternative(hit, "secondary prevention after stroke")["regimen"],
                         "aspirin_dipyridamole")
        # Dipyridamole is not a post-PCI regimen; the rule set must not offer it.
        self.assertEqual(rules.choose_alternative(hit, "dual antiplatelet therapy following PCI for ACS")["regimen"],
                         "ticagrelor_aspirin")

    def test_every_rule_set_is_well_formed(self):
        genes = set(registry.known_genes())
        for rs in registry.rule_sets():
            with self.subTest(rule=rs["rule_id"]):
                self.assertIn(rs["gene"], genes)
                self.assertTrue(rs["rule_version"])
                self.assertTrue(rs["drugs"])
                known = set(registry.gene_def(rs["gene"])["phenotypes"])
                for key, rec in rs["recommendations"].items():
                    self.assertIn(key, known, f"{rs['rule_id']} references unknown phenotype {key}")
                    self.assertIn(rec["action"], {"switch", "dose_reduce", "hold",
                                                  "contraindicated", "review", "no_change"})
                    self.assertIn(rec["severity"], {"critical", "high", "moderate", "none"})


class TestSafety(unittest.TestCase):
    def test_allergy_blocks(self):
        rec = {"allergies": [{"term": "Aspirin"}], "problems": [], "medications": []}
        checks = safety.check_regimen("aspirin_dipyridamole", {}, rec)
        self.assertTrue(safety.blocking_failures(checks))

    def test_anticoagulant_interaction_blocks(self):
        rec = {"allergies": [], "problems": [], "medications": [{"term": "Apixaban 5mg"}]}
        self.assertTrue(safety.blocking_failures(safety.check_regimen("ticagrelor_aspirin", {}, rec)))

    def test_caution_does_not_block(self):
        rec = {"allergies": [], "problems": [{"term": "Asthma"}], "medications": []}
        checks = safety.check_regimen("ticagrelor_aspirin", {}, rec)
        self.assertEqual(safety.blocking_failures(checks), [])
        self.assertTrue(safety.cautions(checks))

    def test_clean_record_passes(self):
        rec = {"allergies": [], "problems": [], "medications": []}
        self.assertEqual(safety.blocking_failures(safety.check_regimen("aspirin_dipyridamole", {}, rec)), [])


class TestLlmLayer(unittest.TestCase):
    """The model may add, but never overwrite what the patterns read directly."""

    def test_hallucinated_quote_is_dropped(self):
        ex = LlmExtractor(lambda _: '[{"gene":"CYP2C19","alleles":["*2","*2"],'
                                    '"evidence":"a sentence that is not in the source at all"}]')
        self.assertEqual(ex.extract("Discharged home well.", SRC, "SIM-000001"), [])

    def test_model_failure_is_contained(self):
        def boom(_):
            raise RuntimeError("provider down")
        self.assertEqual(LlmExtractor(boom).extract("CYP2C19 *2/*2", SRC, "SIM-000001"), [])

    def test_pattern_wins_a_conflict(self):
        pattern = EX.extract("CYP2C19 *1/*1 normal metaboliser.", SRC, "SIM-000001")
        llm = LlmExtractor(lambda _: '[{"gene":"CYP2C19","alleles":["*2","*2"],'
                                     '"evidence":"CYP2C19 *1/*1 normal metaboliser."}]')
        merged = reconcile(pattern, llm.extract("CYP2C19 *1/*1 normal metaboliser.", SRC, "SIM-000001"))
        self.assertEqual(normalise(merged)[0].phenotype, "normal_metaboliser")

    def test_model_may_add_an_unseen_gene(self):
        pattern = EX.extract("CYP2C19 *1/*1.", SRC, "SIM-000001")
        llm = LlmExtractor(lambda _: '[{"gene":"TPMT","alleles":["*3A","*3C"],"evidence":"TPMT"}]')
        merged = reconcile(pattern, llm.extract("TPMT", SRC, "SIM-000001"))
        self.assertEqual({f.gene for f in merged}, {"CYP2C19", "TPMT"})


class TestDocumentTraversal(unittest.TestCase):
    def test_named_sections_are_found(self):
        res = {"id": "r-1", "kind": "discharge-summary", "title": "Letter",
               "patientId": "SIM-1", "data": {"sections": {"results": "CYP2C19 *2/*2"}}}
        self.assertIn(("results", "CYP2C19 *2/*2"), iter_text_blocks(res))

    def test_section_arrays_are_found(self):
        res = {"id": "r-1", "kind": "note", "title": "Note", "patientId": "SIM-1",
               "data": {"sections": [{"heading": "Genomics", "text": "TPMT *2/*3A"}]}}
        self.assertIn(("Genomics", "TPMT *2/*3A"), iter_text_blocks(res))


if __name__ == "__main__":
    unittest.main(verbosity=2)
