# Third-party notices

The three-form implementation and its narrow test cases were written for this task.
Design context: `reports/codegen-product-kit-27/report.md` sections 2, 3, 6 and
`reports/codegen-product-kit-28/report.md` Q3/Q4, produced on 2026-10-04. Those research
prototypes informed the explicit clock/zone, span-removal and UTC-offset requirements;
no prototype source, corpus data or lexicon table was copied. Examples required by the
task were re-derived under the narrow clock policy. No chrono-node, Recognizers-Text,
Duckling, dateparser or other upstream parser code, tables or test assets are included.
The library source is covered by the accompanying MIT LICENSE.

The only separately installed runtime dependency is `tzdata` (Python tzdata project,
https://github.com/python/tzdata), licensed under Apache-2.0. Its IANA timezone data is
public domain. That distribution carries its own licenses and notices; it is not vendored
here. Python's datetime, regex and zoneinfo modules are part of the standard library.
