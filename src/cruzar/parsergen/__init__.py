"""Parser-generation tooling (plan 030 / 029).

This subpackage produces a privacy-safe, structurally-faithful *anonymized layout bundle*
from a real statement, so a new institution's parser can be developed without exposing real
values. It is **operator-run tooling only**: imported by the ``cruzar anonymize`` command and
its tests, and NEVER by ``cruzar.pipeline`` — a normal ingest run gains no dependency on it and
the offline suite stays offline.
"""
