# Design and API findings

## The workflow

A scheduler, simulator-event poller, CLI invocation, or manual frontend request starts a durable scan. Each patient is loaded from the directory, then every page of their GP and hospital resources is retrieved. Shared resources without a patient ID are excluded from that patient's evidence and duplicate records across scopes are collapsed by resource ID.

An **evidence-review ADK agent** extracts a structured set of gene/result/subject/status/drug/indication facts from primary clinical documents. The backend resolves citations from exact resource fields. This avoids treating an LLM-generated quotation as source text. Existing GP derivative coding and raw generated panels remain available to the GP reviewer, but do not independently manufacture a diplotype.

The **deterministic policy matcher** handles the user's twelve rules. The sample cohort does not determine whether a rule fires. Pending results, family history, normal genotypes and negative HLA screens are explicit outcomes rather than failures of the rule engine.

An **independent GP ADK agent** reviews the original patient context, records, proposed action and contraindications. It can accept, deny or defer. A recorded death at simulator time also activates a deterministic execution block. Existing medication replacement notes and rejected prescriptions are material context, not ignored because the original letter still contains clopidogrel.

The **executor** re-fetches the patient and records, verifies that the relevant snapshot still matches the reviewed one, and accepts only the compiled policy plan. A durable outbox is written before each API call. Retried requests use the original UUID as `Idempotency-Key` and the exact original body. Receipts and ADK session IDs are preserved for review. No model gets unrestricted simulator write tools.

## Why ADK plus deterministic orchestration

The ADK library supports schema-defined outputs, isolated contexts, model-provider adapters and event-sourced sessions. Model calls are contained in `app.agent`; job lifecycle, evidence validation, rule matching and execution gates are deterministic application code. ADK's actual `0.6.0` package exports a SQLite session store even though an older reference file says otherwise; the implementation uses the installed export. The package requires Zod 3.25 and OpenAI SDK 5 peers, so those versions are respected.

SQLite is a good fit for one local worker, gives durable state across restarts and avoids deploying a database for this prototype. For horizontal scaling, replace job/outbox persistence with PostgreSQL, use ADK's PostgreSQL store, and claim jobs with transactional leases. The frontend never owns workflow state.

## Simulator API surface

- `GET /api/team`: authenticated team and scopes. The supplied key has GP, hospital, community, pharmacy, diagnostics, referrals and wearables access.
- `GET /api/sites/gp/patients`: fixed 30-patient pages using `offset`; exact patient lookup uses `q` and then verifies the returned ID.
- `GET /api/sites/{site}/view`: scoped resources, `patient`, `offset`, `limit` (maximum 500), and `resourceTotal`. Even patient-filtered views can contain unassigned shared resources.
- `GET /api/sites/gp/documents`: GP-visible discharge correspondence plus linked patients. This is the efficient discovery path for the supplied examples.
- `GET /api/sites/hospital/genomes` and `/consultations`: complete secondary-care records, including stored versions/provenance. Hospital scope is required; raw SNP panels are not full interpreted diplotypes.
- `GET /api/clock`: simulator time and at most 100 recent visible events. There is no documented general clinical event subscription/webhook registration endpoint.
- `POST /api/sites/gp/actions`: tasks, referrals, consultation records, problems, draft prescriptions, appointment booking and versioned transitions. The API accepts `Idempotency-Key`, with a `clientRequestId` body alternative. Versioned updates use `expectedVersion`.
- `POST /api/control/model-propose`: operator-only and does not execute proposals. It is not used as a GP decision service with this team key.

The world contains 50,000 synthetic patients. The authored PGx discharge summaries are present. Current world state differs from the original scenario: deceased patients, rejected prescriptions, existing replacement-medication notes and unphased generated SNP fixtures all coexist with the authored letters. Rules therefore require fresh clinical context, not hardcoded outcomes for listed IDs.

## Mapping acceptance to simulator effects

| Policy outcome | Implemented effect |
|---|---|
| Alternative / avoidance / specialist dose review | GP task plus hospital referral naming the responsible specialist |
| HLA-B*57:01 positive | Above, plus a persistent active PGx problem alert |
| Pending result | GP/laboratory chase task |
| Family history only | GP task to consider patient testing; no patient phenotype assignment |
| Normal / negative result | Saved consultation documenting the result and no PGx-driven change |
| WRITE TEST | Audit-only exclusion |

The simulator has a coarse service target (`hospital`), not a documented specialist enumeration. The referral title and text name stroke, cardiology, oncology, HIV or gastroenterology explicitly. Task urgency is included in the title; the documented generic action schema does not expose a priority field.

A future medication policy needs an explicit specialist-approved medication order (drug, dose, unit, route, frequency, duration, quantity and indication), eligibility checks, pharmacy/GP ownership handling, current versions for transitions and a defined plan for maintaining coverage. Appointment actions need an actual available session/slot. Neither should be guessed from these referral-oriented rules.

## Reliability boundaries

- A full scan currently processes patients sequentially. This bounds API/model load but is slow for 50,000 records; production should first maintain an incremental local index and use a bounded worker pool.
- The simulator's offset pagination does not provide a snapshot token. Active-world changes during a long scan may require a later reconciliation; this implementation does not pause or alter the user's clock.
- Recent events are an optimization, not a complete change-data-capture log. Full scheduled reconciliation remains necessary for coverage outside PGx documents.
- A completed episode is not automatically executed again. Its history is retained. Recurrent clinically distinct episodes need new evidence; clinical resolution/reopening is an extension point.
- Snapshot revalidation narrows but cannot eliminate a race between the final read and an unversioned create action. Existing resource updates require `expectedVersion` when implemented.
- Partial multi-action completion is visible, never reported as overall success. Uncertain requests retain their original payload and idempotency key.
- The rule set is structured and reviewed in source; arbitrary uploaded free text is not a live executable policy editor.

## Sources inspected

- [Anima ADK repository and source](https://github.com/mycontinuum-com/adk)
- [Simulator API guide](https://sim.animahacks.com/docs/api/)
- [Simulator OpenAPI specification](https://sim.animahacks.com/api/openapi.json)
- [API Explorer](https://sim.animahacks.com/docs/explorer/)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [CPIC CYP2C19/clopidogrel guideline](https://pmc.ncbi.nlm.nih.gov/articles/PMC9287492/)
- [DPYD/capecitabine prescribing recommendations](https://www.ncbi.nlm.nih.gov/books/NBK385155/)
- [CPIC thiopurine 2025 update](https://files.cpicpgx.org/data/guideline/publication/thiopurines/2026/41618934.pdf)

The HLA-B guideline link supplied by the user was retained; automated retrieval encountered a browser challenge, so the implemented rule preserves the user's supplied text rather than claiming a fresh independent review of that page.
