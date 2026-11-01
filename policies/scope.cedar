// Scope policies for autonomous agents acting through the SMO, over the action vocabulary
// (read_kpi, what_if, set_tilt, cell_off, cell_on). Every policy carries an @id; that ID is cited in
// every denial and in the ledger. Edited live: the policy engine reloads this file on change.
//
// Context per action, filled by the enforcement point from network state:
//   batch_size              distinct cells in the request
//   cells_changed_this_tick distinct cells this agent changed this hour, including this request
//   cell_load               users attached to the target cell now (from the twin)
//   input_flagged           the agent recently read input that screening flagged
//
// These are per-action rules. Whether an allowed action is safe given the other agents' intents,
// their recent changes and the cumulative effect is decided after this, by the impact assessor.

@id("GLOBAL-ADMITTED-ONLY")
forbid (principal, action, resource)
unless { principal.admitted };

// ---- Energy agent (RAN energy team; wraps the MAVERIC energy-saving RL model) -------
@id("ES-READ")
permit (principal == Rapp::"energy-saving-rl", action in [Action::"read_kpi", Action::"what_if"], resource);

@id("ES-ON")
permit (principal == Rapp::"energy-saving-rl", action == Action::"cell_on", resource);

@id("ES-TILT")
permit (principal == Rapp::"energy-saving-rl", action == Action::"set_tilt", resource);

@id("ES-OFF-LOW")
permit (principal == Rapp::"energy-saving-rl", action == Action::"cell_off", resource)
when { resource.criticality == "low" };

@id("ES-OFF-MEDIUM")
permit (principal == Rapp::"energy-saving-rl", action == Action::"cell_off", resource)
when { resource.criticality == "medium" };

// ---- Load-balancing agent (RAN capacity team) ---------------------------------------
@id("LB-READ")
permit (principal == Rapp::"load-balancing-agent", action in [Action::"read_kpi", Action::"what_if"], resource);

@id("LB-TILT")
permit (principal == Rapp::"load-balancing-agent", action == Action::"set_tilt", resource)
when { resource.criticality != "critical" };

@id("LB-ON")
permit (principal == Rapp::"load-balancing-agent", action == Action::"cell_on", resource);

// ---- External agent (used only in the robustness tests: reads alarm text, read-only) --
@id("3P-READ")
permit (principal == Rapp::"third-party-demo", action in [Action::"read_kpi", Action::"what_if"], resource);

@id("3P-READ-ONLY")
forbid (principal == Rapp::"third-party-demo", action, resource)
unless { action in [Action::"read_kpi", Action::"what_if"] };

// ---- Operator guardrails (apply to every agent) -------------------------------------
@id("GLOBAL-NO-OFF-CRITICAL")
forbid (principal, action == Action::"cell_off", resource)
when { resource.criticality == "critical" };

// A cell may only be switched off while it carries few users (RAN engineering's existing rule).
@id("GLOBAL-OFF-ONLY-LOW-LOAD")
forbid (principal, action == Action::"cell_off", resource)
when { context.cell_load > 80 };

// Blast radius budget: distinct cells changed this tick, including this batch.
@id("GLOBAL-BLAST-BUDGET")
forbid (principal, action, resource)
when { action != Action::"read_kpi" && action != Action::"what_if" &&
       context.cells_changed_this_tick > principal.max_cells_per_tick };

// An agent whose recent input was flagged by screening may only read.
@id("GLOBAL-TAINTED-INPUT-READ-ONLY")
forbid (principal, action, resource)
when { context.input_flagged && action != Action::"read_kpi" && action != Action::"what_if" };

// ---- Orchestrator agent (splits operator intents into tasks; delegates every change) --
@id("ORCH-READ")
permit (principal == Rapp::"orchestrator-agent", action in [Action::"read_kpi", Action::"what_if"], resource);

@id("ORCH-READ-ONLY")
forbid (principal == Rapp::"orchestrator-agent", action, resource)
unless { action in [Action::"read_kpi", Action::"what_if"] };
