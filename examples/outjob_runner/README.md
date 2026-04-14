# outjob_runner

Run an Altium OutJob from Python through the automatic OutJob runner.

This example uses the RT Super C1 project assets and a static
`reference_gen.OutJob` stored next to the project. The script stages a working
copy under its `output/` folder so Altium writes generated files next to that
working PrjPcb instead of modifying the source assets.

The sample demonstrates the high-level runner path:

```python
prj = AltiumPrjPcb(PROJECT_FILE)
result = prj.outjob().run(
    timeout_seconds=300,
)
```

The project-bound OutJob handle launches Altium Designer, runs the OutJob
script, and waits for a completion marker. Because the OutJob is part of the
loaded project, the sample runs that associated OutJob directly in the working
copy and lets Altium resolve the project documents from the PrjPcb.

## Safe Prepare Mode

By default the example does not launch Altium. It resolves the project OutJob
through `prj.outjob()` and writes a summary JSON:

```powershell
uv run python examples\outjob_runner\outjob_runner.py
```

## Run Altium

To actually launch Altium Designer and run the OutJob:

```powershell
uv run python examples\outjob_runner\outjob_runner.py --run --timeout 300
```

After a successful run the script opens the generated output folder in the
system file browser. Use `--no-open-output` to suppress that behavior in
automation:

```powershell
uv run python examples\outjob_runner\outjob_runner.py --run --no-open-output
```

The generated files are written to the working project copy:

```text
examples/outjob_runner/output/assets/projects/rt_super_c1/outputs/generated/
```

The runner waits for the Altium script completion marker. That marker is
written after `WorkspaceManager:GenerateReport` returns and the script closes
the OutJob/project documents. It does not require the Altium `X2.exe` process
to exit; Altium may remain open after the sample completes.

Temporary run scripts, marker files, and logs are written to:

```text
examples/outjob_runner/output/run_artifacts/
```

## Inputs

```text
examples/assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb
examples/assets/projects/rt_super_c1/RT_SUPER_C1.SchDoc
examples/assets/projects/rt_super_c1/RT_SUPER_C1.PCBdoc
examples/assets/projects/rt_super_c1/reference_gen.OutJob
```

## Output

```text
examples/outjob_runner/output/outjob_runner_summary.json
```
