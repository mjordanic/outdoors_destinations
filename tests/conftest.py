import os

# Before any test module imports numpy: forked workers need Apple Accelerate on
# one thread (dayhike.parallel, ADR-0044).
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
