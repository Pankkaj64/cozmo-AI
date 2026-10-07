"""Library Contents Claim Agent backend package.

Runs before any submodule is imported, so environment switches that must precede heavy
imports live here. PaddleOCR imports `transformers`, which fails on machines that also have
TensorFlow + Keras 3 unless TensorFlow is switched off first.
"""

import os

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_TORCH", "1")
