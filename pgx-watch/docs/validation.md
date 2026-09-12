# Live validation

Validated against the supplied Anima world on 12 September 2026. All records are synthetic.

## Final result

- 15 sample patients reviewed successfully with zero errors in the final two scans.
- 10 executed findings; 4 denied findings; 1 WRITE TEST exclusion.
- 19 successful simulator actions: 8 GP tasks, 8 hospital referrals, 2 consultation records and 1 persistent PGx problem alert.
- All 19 resources were subsequently retrieved from the simulator by patient and matched to their returned resource IDs.
- A repeat scan with execution enabled completed with zero new findings and zero additional simulator writes.
- Earlier failed extraction/review attempts remain visible in scan history; they were blocked and subsequently repaired. A duplicate test-exclusion entry from an earlier identity format is retained as superseded history.

| Patient | Policy outcome | Final state |
|---|---|---|
| SIM-000002 | EXCLUDE_TEST_EVIDENCE | excluded |
| SIM-000011 | RECOMMEND_ALTERNATIVE | denied |
| SIM-000012 | CONSIDER_ALTERNATIVE | denied |
| SIM-000013 | VERIFY_RESULT_AND_REVIEW | executed |
| SIM-000014 | SPECIALIST_DOSE_REVIEW | executed |
| SIM-000015 | AVOID_DRUG | executed |
| SIM-000016 | AVOID_STANDARD_DOSE_AND_ESCALATE | executed |
| SIM-000017 | NO_PGX_CHANGE | executed |
| SIM-000018 | CHASE_RESULT | denied |
| SIM-000019 | NO_PATIENT_GENOTYPE | denied |
| SIM-000020 | NO_PGX_RESTRICTION | executed |
| SIM-000021 | RECOMMEND_ALTERNATIVE | executed |
| SIM-000022 | RECOMMEND_ALTERNATIVE | executed |
| SIM-000041 | RECOMMEND_ALTERNATIVE | executed |
| SIM-000051 | RECOMMEND_ALTERNATIVE | executed |

## Automated checks

- 12 tests pass, including a scripted ADK run with no live model call.
- Backend TypeScript check passes.
- Frontend production build passes.
- Local frontend HTML and API proxy respond successfully.
- No browser interaction or visual QA was performed; it was not requested.

## Scope and remaining limits

- The complete 50,000-patient scan is implemented but was not run end-to-end during this validation.
- Live writes were limited to the supplied sample policy. No medication orders were drafted and no appointments booked.
- Automation is implemented but remains disabled until selected in the interface; the service is local, not installed as an always-on daemon.
- Frontend dependency remediation removed the inherited critical advisory. The bundled Vinext/Cloudflare development toolchain still reports 14 dependency advisories (4 moderate, 10 high); resolving the remaining report requires a separately validated framework/toolchain upgrade. Do not expose development servers to an untrusted network.
- Backend dependency installation reported zero known advisories.
- GP review is model-based. The deterministic death gate overrode attempted retrospective acceptance for deceased patients; both the agent session and final blocked outcome are preserved.
