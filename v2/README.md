# Site Factory v2 — Astro Experiment

This is a side-by-side experiment. It does not replace the current `scripts/`
pipeline.

Goal: prove whether an Astro static-site generator can produce simpler, faster,
more reliable MKB demo sites than the current Next.js static-export flow.

## First Vertical Slice

Input:

- existing `data/<slug>/briefing.md`
- optional `data/<slug>/structured_data.json`
- optional `data/<slug>/images.json`
- optional `data/<slug>/pages.json`

Output:

- `artifacts/v2/<slug>/site_plan.json`
- `artifacts/v2/<slug>/astro-source/`

Run:

```bash
python3 v2/scripts/build_from_existing_brief.py --slug kapsalon-frank-nl
```

Then, if you want to build the Astro output:

```bash
cd artifacts/v2/kapsalon-frank-nl/astro-source
npm install
npm run build
```

If the host has no `npm`, use the existing Docker image:

```bash
docker run --rm \
  -v /home/ryan-poser/site-factory:/workspace \
  -w /workspace/artifacts/v2/kapsalon-frank-nl/astro-source \
  site-factory-worker \
  sh -lc 'npm install && npm run build'
```

## Design Principle

V2 should use AI primarily to produce structured site plans, not free-form app
code. This initial slice uses deterministic extraction from the existing brief;
the next step can swap in an LLM planner that writes the same `site_plan.json`
shape.
