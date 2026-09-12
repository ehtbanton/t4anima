# PGx Bridge

Makes pharmacogenomic results that already exist fire at the moment someone
writes a prescription — however many years later.

The NHS is already generating these answers. NICE recommends CYP2C19 testing
after stroke and TIA; DPYD before fluoropyrimidines has been mandated since
2020; TPMT before azathioprine and HLA-B\*57:01 before abacavir for longer than
that. The result lands in a discharge summary, a genomics report, a PDF
attachment — and dies there. A genotype is valid for the rest of a patient's
life. The document it arrived in gets read once, if at all.

This is not a testing problem and it is not a clinical-evidence problem. It is
an information-flow problem that is nobody's job.

## What it does

```
secondary care record ──▶ extraction ──▶ phenotype store ──▶ prescribing hook
  discharge summaries      free text       SNOMED-coded        fires forever
  clinic letters           to findings     in the GP record    after
  lab reports
                                                                    │
                     GP one-click decision ◀── safety checks ◀──────┘
                              │                 this patient's
                              │                 allergies, problems,
                              ▼                 interactions
              authorised by a named clinician
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       issue replacement  cancel repeat  tell the patient
                              │
                              ▼
                  chase at 48h if nothing moves
```

Nothing in that pipeline is specific to CYP2C19. Swap the rule set and the same
machinery covers DPYD before capecitabine, TPMT before azathioprine, HLA-B
before abacavir or carbamazepine, CYP2D6 before codeine. The extraction layer,
the phenotype store and the prescribing hook are the product, and they are
gene-agnostic from the first commit.

## Layout

| Path | What it is |
|---|---|
| `pgxbridge/extract.py` | Perception. Pattern extractor (deterministic, always runs) plus an optional LLM extractor for narrative. |
| `pgxbridge/phenotype.py` | Normalisation. Findings → one SNOMED-coded phenotype per gene. |
| `pgxbridge/rules.py` | The deterministic rule engine. No model runs here. |
| `pgxbridge/safety.py` | Contraindication checks against this patient's own record. |
| `pgxbridge/agent.py` | Orchestration: scan, code, hook, draft, route, apply, chase. |
| `pgxbridge/registry/genes.json` | Gene definitions, allele functions, SNOMED codes. Data, not code. |
| `pgxbridge/registry/rules/*.json` | One versioned rule set per drug–gene pair. |
| `pgxbridge/sim.py` | The only file that knows what record system it is talking to. |
| `demo/` | Seeds a 12-patient scenario and runs the pipeline end to end. |
| `tests/` | 47 offline tests. No network, no API key. |

## Run it

```bash
export NHS_SIM_KEY=sim_...
python3 demo/seed.py        # create the cohort (once per world)
python3 demo/run_demo.py    # watch the agent work the pathway
python3 -m unittest discover -s tests
```

CLI:

```bash
python3 -m pgxbridge.cli registry                     # what rules are in force
python3 -m pgxbridge.cli scan --patients SIM-000011   # extract and code
python3 -m pgxbridge.cli hook --patient SIM-000011    # fire against current meds
python3 -m pgxbridge.cli run  --patients SIM-000011,SIM-000012
python3 -m pgxbridge.cli authorise --task r-123 --clinician "Dr A Patel (GMC 1234567)"
python3 -m pgxbridge.cli chase
```

## Design decisions worth arguing with

**The model does perception and orchestration. It does not make clinical
decisions.** Every recommendation comes from a versioned JSON rule set that a
pharmacist can review without reading Python, and every output carries the rule
id and version that produced it. `CYP2C19-CLOPIDOGREL v1.3.0` is on the face of
the alert and in the audit ledger.

**The pattern extractor is the floor, not the fallback.** Diplotype strings,
rsIDs and HLA carriage are regular enough that a regex is the right tool and can
be unit-tested against a corpus. The LLM layer exists for narrative that the
patterns miss, is optional, never overwrites a diplotype the patterns read
directly, drops any finding whose quoted evidence is not in the source text, and
cannot take the pipeline down when the provider fails.

**The dangerous failure is a false positive, not a false negative.** A missed
result leaves things exactly as they are today. A *wrong* result puts someone
else's genotype on a live record. So the extractor refuses family-history
sentences ("her mother is a poor metaboliser"), refuses pending tests ("sample
sent, result awaited"), and handles the fact that "not detected" contains
"detected". Those cases are negative controls in the demo cohort and tests.

**The record is the source of truth, not the agent's ledger.** If the phenotype
is already coded, the agent adopts it rather than writing a duplicate. If the
replacement regimen is already on the record, authorisation returns
`superseded` and prescribes nothing. Two agents racing produce one outcome.

**Alert fatigue is a safety failure.** Routing is idempotent — a finding already
sitting in someone's inbox is not raised again. The chase has a cooldown so an
unactioned task escalates once per interval, not once per cron tick.

**Not everything is a one-click switch, and pretending otherwise is the failure
mode.** A DPYD dose reduction goes to oncology with a computed dose. An
HLA-B\*57:01 carrier on abacavir is a contraindication, not a substitution. A
TPMT poor metaboliser on azathioprine is a hold. Where every alternative fails
the safety checks — the demo's aspirin-allergic stroke patient — the agent
escalates with its working shown and offers nothing.

## What the demo proves

Twelve patients. Eight should fire, four are negative controls.

| Case | Finding, and where it was hiding | Outcome |
|---|---|---|
| CYP2C19 \*2/\*2 | structured lab block | switch to aspirin + dipyridamole MR |
| CYP2C19 \*1/\*2 | one clause of discharge narrative | switch to aspirin + dipyridamole MR |
| CYP2C19 rs4244285 | in "GP actions", gene never named | switch to aspirin + dipyridamole MR |
| CYP2C19 \*2/\*3, post-PCI | structured lab block | ticagrelor + aspirin — indication rules out dipyridamole |
| CYP2C19 \*2/\*2, aspirin allergy | structured lab block | **escalated** — no safe alternative |
| DPYD c.2846A>T | pre-chemo screen | **escalated to oncology** with a 50% dose |
| HLA-B\*57:01 positive | screening result | **escalated** — contraindication, not a switch |
| TPMT \*3A/\*3C | structured lab block | **escalated to specialist** — hold |
| CYP2C19 \*1/\*1 | normal result | silent |
| CYP2C19, sample sent | result pending | silent |
| mother is a poor metaboliser | family history | silent |
| HLA-B\*57:01 not detected | negative screen | silent |

Extraction is correct on all twelve. The clock is then advanced past 48 hours to
show the chase firing on what nobody touched, and not firing twice.

## Before real use

**The SNOMED CT identifiers in `registry/genes.json` are placeholders.** They
are structurally valid so the pipeline runs end to end, and `codes_validated` is
`false` with a warning printed by `pgx registry`. They must be replaced with
released concept IDs from the UK SNOMED CT Clinical Edition, ideally aligned to
the PRSB pharmacogenomics standard, before this touches a real record. A test
asserts they are well-formed and unique; no test can assert they are *right*.

The rule sets encode CPIC and NICE guidance but have not been through clinical
governance. They are the artefact a medicines optimisation committee should
argue over and sign, which is the point of keeping them as reviewable data.

`sim.py` talks to the NHS-SIM training estate. A real deployment replaces that
one file with GP Connect, EPS and an EPR integration; nothing above it changes.

All data in the demo is synthetic. No real patient data, NHS credentials or
clinical advice.

## Why nobody has built this

QOF indicator STIA007 pays four points for recording that a stroke or TIA
patient is on an antiplatelet or anticoagulant. Not the right one — any one. A
practice can hit the top threshold with a third of its stroke register on a drug
that does nothing for them. No indicator anywhere in the framework touches
genotype, drug efficacy, or whether a prescription is actually working.

So the pathway is unmeasured, therefore unincentivised, therefore unoptimised.
That is worse than a gap in testing, because the answer already exists and
simply never reaches the person holding the prescription pad. Which is also why
the fix cannot be another dashboard asking an overloaded GP to care about a
metric they are not scored on. It has to be something that works the pathway
itself.
