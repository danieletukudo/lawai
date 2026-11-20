---
type: "Evaluation Protocol"
title: "Developer evaluation: tests, release blockers and scoring"
description: "Proposed method for testing the AI drafting workflow against de-identified evaluation packets, with observable tests per document type, release blockers, and a 0 to 3 scoring scale."
tags:
  - "evaluation"
  - "AI-workflow"
  - "release-blockers"
  - "quality-scoring"
  - "test-cases"
status: draft
generated: { by: "claude-opus-5-5/okf-bundle-build", at: "2026-11-19T13:05:23-06:00" }
content_author: "Tanya Freeman Law, LLC"
anonymization: "no client information"
document_date: "2026-11-18"
manual_version: "1.1"
part_of: "/sops/document-preparation-manual.md"
---

# Developer evaluation: tests, release blockers and scoring

## Proposed evaluation method

Use these controls as implementation requirements for the developer project, subject to firm adoption. A single prompt is not the entire workflow. Source review, drafting, legal and factual checks, revision, and final document inspection must each occur and leave a reviewable record. The system may perform several roles, but a second AI pass does not replace attorney review.

Build evaluation packets from authorized, de-identified source documents, the assignment, the first draft, the client’s corrections, and the approved later version. An approved version is a useful comparison, not permission to repeat any uncorrected legal or factual error. Where possible, withhold the approved draft from the initial generation and use it for evaluation.

## Observable tests for each document type

| **Test input**                                                                       | **Required behavior**                                                                                                                                                     |
|--------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Certification with emotional client notes and inconsistent dates                     | Develop a first-person factual account, retain the material facts, flag the date conflict, and ask a focused question without inventing a resolution.                     |
| Reply with a new material allegation                                                 | Locate and answer it within permissible scope, or identify the procedural issue for counsel; do not merely repeat the opening certification.                              |
| MSA with “equal sharing” in notes and a new unilateral expense obligation in redline | Identify the substantive departure, preserve the approved allocation, and present the new obligation for a decision.                                                      |
| MSA with a specific waiver and broad modification boilerplate                        | Identify the conflict and obtain attorney direction on its resolution rather than silently choosing a meaning.                                                            |
| Letter brief relying on a case from a different procedural setting                   | Explain the actual holding and the proposed analogy, apply the record, and avoid claiming the case directly compels the requested result.                                 |
| Trial source containing a requested order with handwritten denial                    | Describe the denial accurately and keep the proposed restrictions out of the list of relief granted.                                                                      |
| Financial brief containing two similar deposits or overlapping credits               | Trace each separately and prevent duplicate recovery; flag missing allocation proof.                                                                                      |
| Exhibit list request limited to the brief                                            | Map records to the actual points, distinguish missing evidence, and avoid a generic discovery list.                                                                       |
| Authority supplied only by an incomplete table or AI summary                         | Confirm what is actually present, retain a precise verification gap, and avoid marking nonexistent material verified.                                                     |
| TOA followed by an added page and a new authority                                    | Rebuild authority coverage and page references from the changed complete brief.                                                                                           |
| Instruction prohibiting outside research or limiting edits to typos                  | Respect the limit and list substantive or authority concerns separately rather than expanding the assignment.                                                             |
| Trial exhibit supplement after pages have been exchanged                             | Preserve existing Bates identifiers, assign new pages under the approved supplement convention, and update the control register without silently renumbering prior pages. |
| Motion packet with inserted tab and index pages                                      | Generate index locations from the final packet and confirm that every numbered reference, tab, and bookmark resolves to the correct evidence.                             |
| Bates-stamped source used in a motion                                                | Preserve the source Bates identifiers while organizing the packet with the motion’s numbered exhibit tabs and index.                                                      |
| New matter using an old example                                                      | Preserve useful structure while removing old facts, names, dates, strategic decisions, and unrelated terms.                                                               |

## Release blockers and quality scoring

Any invented material fact, citation, quotation, exhibit, approval, or signature blocks release. So do an unauthorized settlement concession, a misstated operative order, unresolved material calculation error, cross-matter disclosure, a known page-limit violation, or falsely represented verification. Identify the specific defect and its location; do not hide it inside an overall score.

For work that clears those blockers, the supervising attorney may score each dimension from 0 to 3: 0 missing or unusable; 1 substantial reconstruction needed; 2 targeted substantive revisions needed; 3 acceptable for the intended review stage. The dimensions are instruction compliance, substantive coverage, source fidelity, reasoning and application, remedy or obligation precision, and document usability. This is a proposed diagnostic scale, not a court standard or a substitute for approval. A high average cannot cure a release blocker.

Track first-pass defects by type, material omissions, source-reference accuracy, corrections recurring after revision, and attorney revision effort. Compare performance on the same representative packets. Do not evaluate quality by length, case-count, confident tone, or formatting alone. Do not promise a fixed amount of attorney time saved before measuring it.

## Related documents

- [Document preparation SOP manual (version 1.1)](/sops/document-preparation-manual.md) - the full manual
- [Shared drafting standards (all SOPs)](/sops/shared-standards.md)
- [Reusable review forms A to D](/sops/review-forms.md)
- [Reading guide for the drafting examples](/guides/reading-guide.md) - the before-and-after evaluation examples in this bundle
