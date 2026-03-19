---
name: "Repo Guide"
description: "Project-specific development guidance for this desktop pet workspace."
---

Before editing, inspect the relevant files and preserve existing behavior contracts.

For backend changes:
- keep the SSE event contract stable
- preserve approval_required / done semantics
- prefer targeted tests before full-suite runs

For frontend changes:
- preserve the existing desktop-pet visual language
- avoid moving high-conflict entry files unless necessary

