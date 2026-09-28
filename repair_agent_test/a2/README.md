# A2 — the model-comparison harness

The designer's A2 is **not a screen yet**. It is a measurement: when several local models are shown
the same question and asked to find the defect, **which one did the thing a person did?**

This directory holds the two halves that make that measurable:

1. `build_dataset.py` — turns the reviewed corpus into a **scored dataset**. The ground truth is the
   reviewer's own `correct` event: the text the machine shipped, and the text the person replaced it
   with. Both are real; neither was written for this experiment.
2. `compare_models.py` — shows each model the *shipped* text, asks for the corrected text, and scores
   it against the human correction.

The comparison UI (the screen) is built on this output rather than on vibes, because "which model is
better" without a scored set is a poll.
