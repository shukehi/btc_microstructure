# R104-01 source and independent cases

`original/reproduce_gap_flag.py` is copied byte-for-byte from report PR7 fixed
commit063da7acd32433d9b3fbc8643ee3f4861c03a69d. Its original failure evidence and
report are copied to reports/round1/quality_propagation_fix/ without rewriting
PR7 history. Sources and SHA-256s are in sources.json.

`original/test_r104_quality_review.py.txt` preserves the Python code block from
review5485855471. `checks/test_r104_quality_review.py` is only Ruff-formatted;
AST equivalence is verified in review-equivalence.json, with no changed assertions.
Before: normal locked Python3.12.13/PyArrow20.0.0 imports,2failed/2passed and
original Parquet assertion exit1. After: independent4pass and original script
exit0. Both are explicitly executed in offline CI; no extracted/stub Arrow APIs.
