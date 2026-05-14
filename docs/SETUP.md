# Setup guide

## Step 1: Create the GitHub repository

Go to [github.com/new](https://github.com/new) and create a new repository:
- Name: `vlsi-pd-staff-engineer`
- Description: "Multi-agent framework for RTL-to-GDSII physical design using Claude Code"
- Public or Private (your choice)
- Do NOT initialize with README (we already have one)

## Step 2: Push from your WSL terminal

```bash
# 1. Extract the tarball
cd ~/projects
tar xzf pd-test-project.tar.gz   # or wherever you downloaded it
cd pd-test-project

# 2. Initialize git
git init
git add -A
git commit -m "Initial commit: VLSI PD staff engineer multi-agent framework

9 agents, 10 skills, hooks system for RTL-to-GDSII flow.
Adapted from FareedKhan-dev/claude-code-staff-engineer for VLSI PD.

Agents: synthesis, floorplan, placement, CTS, routing, signoff,
        LEC verifier, low-power verifier, forensic ECO debugger.

Includes test counter example and validation script."

# 3. Connect to GitHub and push
git branch -M main
git remote add origin https://github.com/<YOUR-USERNAME>/vlsi-pd-staff-engineer.git
git push -u origin main
```

Replace `<YOUR-USERNAME>` with your GitHub username.

## Step 3: Add the sample run output

After running the test on your machine:

```bash
# Run the test and save the output
cd examples/test-counter
./test_framework.sh 2>&1 | tee sample_run_output.txt

# Commit the sample run
git add sample_run_output.txt
git commit -m "Add sample run output from local machine"
git push
```

## Step 4: Add your Sky130 library setup note

The .gitignore excludes .lib files (they're too large for git).
Add a note in the README or a setup script telling users how to get them:

```bash
# From Docker (fastest)
mkdir -p lib
docker cp $(docker create --rm openroad/orfs:latest):/OpenROAD-flow-scripts/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib ./lib/
```

## Optional: Add topics/tags on GitHub

Go to your repo settings and add topics:
`vlsi` `physical-design` `claude-code` `eda` `sky130` `openroad` `yosys`
`multi-agent` `rtl-to-gdsii` `asic-design`

These help people find your repo.
