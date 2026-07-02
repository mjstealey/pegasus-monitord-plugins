"""Standalone pegasus-monitord plugin emitting workflow-monitor JSONL.

The plugin class lives in :mod:`monitord_wfevents.plugin`; monitord loads it
directly via the ``pegasus.monitord.plugins`` entry point, so this package
init stays import-free.
"""

__version__ = "0.3.0"
