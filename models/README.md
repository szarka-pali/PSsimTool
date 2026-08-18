# models/

Input CAD files (STEP). **They are not versioned** — they are large and often contain a
customer's intellectual property. They belong in artifact storage, not in git.

Paths to them are given in `machines/*.yaml`, in the `step_file` field.

After copying a file in, run the import into the cache:

```bash
uv run pssim import-step models/example.step --machine machines/example.yaml
```
