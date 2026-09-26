"""Shared physical policy constants; units are part of every name."""

# Sleeping storage reports can be reused for at most one week.
LOAD_SOC_CACHE_MAX_AGE_HOURS = 7 * 24
# A source must demonstrate sustained delivery for a full minute.
CASCADE_SOURCE_PROOF_SECONDS = 60
# Numerical allowance for declaring the battery full, shared by all passes.
FULL_SOC_TOLERANCE_PERCENT = 0.1

# Device feedback grace shared by normal loads, cascade OFF and calibration.
ACTOR_CONFIRM_TIMEOUT_S = 30
