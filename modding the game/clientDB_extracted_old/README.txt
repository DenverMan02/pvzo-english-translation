PVZ ONLINE CLIENTDB TRANSLATION WORKSPACE
========================================

EDIT:
    translate\

SOURCE OF TRUTH:
    original\

REFERENCE ONLY:
    text\
    chunks\

For TSV files, translate\ contains only the ID plus likely human-facing
text columns. The repacker takes every other field directly from original\.

Rules:
    - DO NOT change IDs.
    - DO NOT add, delete, or reorder rows.
    - DO NOT change tabs into spaces.
    - You MAY make translated text longer or shorter.
    - Do NOT edit original\ or manifest.json.

The chunks\ folder exists so huge translation files can be opened in pieces.
The authoritative translation file remains translate\<filename>.txt.

Entries: 235
Text entries: 235
TSV entries: 235
