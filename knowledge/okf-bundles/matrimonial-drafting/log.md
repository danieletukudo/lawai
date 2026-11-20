# Log

## 2026-11-19

- **Added** the firm's Document Preparation SOP manual (version 1.1, prepared November 18, 2026) as 11 documents under `sops/`: the manual overview, shared standards, SOPs 1 to 7, developer evaluation, and review forms A to D. The text is unchanged. Bold paragraph headings became markdown headings, bullet characters became list items, and the three New Jersey Judiciary links are now recorded as sources. A line-by-line check confirmed every line of the converted manual is placed.
- **Linked** each certification, brief, summation and MSA to its governing SOP and to the shared standards, both in a `sop` frontmatter list and in its related-documents section. The reading guide links to the developer evaluation, and the SOP summary links to the manual.
- **Added** `guides/firm-sop-summary.md`, the firm's October 20, 2026 SOP summary. Its headings were moved down one level under a single title heading; no wording was changed. It contains no client information.
- **Checked** ten re-uploaded files against the versions already in the bundle. All were byte-for-byte identical, so nothing changed.

## 2026-11-18

- **Created** the bundle from 16 uploaded markdown files, renamed into `guides/`, `examples/`, `work-product/` and `agreements/`. The upload hash prefixes were dropped from the file names.
- **Anonymized** the trial brief and written summation (same case): parties, the daughter and her date of birth, Defendant's girlfriend and her nickname, the experts and their firm, former and opposing counsel, the mediators, the settlement judge, the docket number, residence towns, employers and universities are now bracketed role labels. Case-law citations and the firm's own attorneys are unchanged.
- **Anonymized** the October 30, 2026 pendente lite certification: towns, street addresses, employers, the motor club, jewelers, banks, travel destinations, professions and the county now use the same generic labels as the fictionalized support example. Dates and dollar figures in this file are real and were not changed.
- **Anonymized** the August 4, 2026 reconsideration certification and certification of services (the minor child's name and date of birth, the judge, the proposed parenting coordinator), and the July 17, 2025 cohabitation letter brief (the significant other, his town, the judge).
- **Not anonymized:** the January 15, 2026 ISC statement and the two MSAs beyond the redactions already in their source files.
- **Reformatted** headings only: merged article numbers with their titles in both MSAs (for example, "ARTICLE I: GENERAL PROVISIONS"), rejoined a brief heading that the PDF conversion had split across three lines, added a title heading to bodies that had none, and stripped trailing whitespace. No wording was changed.
- **Validated** with `okf` 0.2.7: `okf validate` reports conformant with OKF v0.2, `okf lint` is clean, and all 27 internal links resolve.
- **Added** OKF v0.2 frontmatter to every concept, links between related documents, and this log and the root index.
