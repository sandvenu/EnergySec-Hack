// Risk tier policies (AP-1). An action is LOW tier (runs at once) only if a policy below
// permits Action::"auto_approve" for it; otherwise it is HIGH tier (shadow run + human approval).
// context.base_action is the vocabulary action, context.batch_size the cells in the request.

@id("TIER-LOW-READ")
permit (principal, action == Action::"auto_approve", resource)
when { context.base_action == "read_kpi" || context.base_action == "what_if" || context.base_action == "cell_on" };

@id("TIER-LOW-TILT")
permit (principal, action == Action::"auto_approve", resource)
when { context.base_action == "set_tilt" && context.batch_size < 3 };

@id("TIER-LOW-OFF")
permit (principal, action == Action::"auto_approve", resource)
when { context.base_action == "cell_off" && context.batch_size == 1 && resource.criticality == "low" };
