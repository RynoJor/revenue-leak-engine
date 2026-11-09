# classifier/

LLM reply classifier. Will hold the prompt, a cached offline run of Claude Haiku over `data/sample/replies.csv`, and an eval that scores predictions against the hidden `true_label` column (accuracy, per-class precision/recall, confusion matrix).

The classifier must never see `true_label`; only the eval reads it.

Placeholder: nothing built yet.
