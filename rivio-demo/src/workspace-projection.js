(function initializeWorkspaceProjection(global) {
  "use strict";

  const PIPELINE_ROLES = [
    { id: "planner", label: "Planner" },
    { id: "research", label: "Research Agent" },
    { id: "analyst", label: "Analyst" },
    { id: "citation", label: "Citation" },
    { id: "writer", label: "Writer" },
    { id: "reviewer", label: "Reviewer" },
  ];

  const STAGE_ROLE = {
    planning: "planner",
    researching: "research",
    analyzing: "analyst",
    reporting: "writer",
  };

  const ACTION_COPY = {
    SEARCH: "检索公开资料",
    FETCH: "采集来源页面",
    READ: "阅读来源片段",
    SUBMIT_EVIDENCE: "提交证据验证",
    FINISH: "完成研究任务",
  };

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function timestamp(value) {
    const parsed = Date.parse(value || "");
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function eventHasHandoff(events, sender, recipient) {
    return events.some((event) => (
      event.event_type === "handoff_created"
      && (!sender || event.data?.sender === sender)
      && (!recipient || event.data?.recipient === recipient)
    ));
  }

  function pipeline(workspace, run, events) {
    const completedStages = new Set(asArray(run?.completed_stages));
    const nodes = PIPELINE_ROLES.map((role) => ({ ...role, status: "pending", detail: "等待接力" }));
    const byId = new Map(nodes.map((node) => [node.id, node]));

    if (completedStages.has("planning")) byId.get("planner").status = "done";
    if (completedStages.has("researching")) byId.get("research").status = "done";
    if (completedStages.has("analyzing")) {
      byId.get("analyst").status = "done";
      byId.get("citation").status = "done";
    }
    if (completedStages.has("reporting")) {
      byId.get("writer").status = "done";
      byId.get("reviewer").status = "done";
    }

    if (eventHasHandoff(events, "professional_research_analyst_agent", "citation_agent")) {
      byId.get("analyst").status = "done";
    }
    if (eventHasHandoff(events, "citation_agent", "professional_writer_agent")) {
      byId.get("citation").status = "done";
    }
    if (eventHasHandoff(events, "professional_writer_agent", "reviewer_agent")) {
      byId.get("writer").status = "done";
    }

    const stage = run?.current_stage || workspace.stage || "";
    let activeRole = STAGE_ROLE[stage] || "";
    if (stage === "analyzing" && byId.get("analyst").status === "done") activeRole = "citation";
    if (stage === "reporting" && byId.get("writer").status === "done") activeRole = "reviewer";
    if (["queued", "running", "stopping"].includes(run?.status) && activeRole) {
      byId.get(activeRole).status = run.status === "stopping" ? "attention" : "working";
    }

    const review = workspace.review;
    const gate = asArray(workspace.qualityGates).at(-1);
    if (review || gate) {
      const reviewer = byId.get("reviewer");
      if (gate?.blocking || review?.approved === false) {
        reviewer.status = "attention";
        reviewer.detail = "质量闸门需要处理";
      } else if (review?.approved || gate?.passed) {
        reviewer.status = "done";
        reviewer.detail = "审查与质量闸门通过";
      }
    }

    const failed = ["failed", "stopped", "interrupted"].includes(run?.status);
    if (failed && activeRole) {
      byId.get(activeRole).status = "attention";
      byId.get(activeRole).detail = run?.message || "执行需要处理";
    }

    nodes.forEach((node) => {
      if (node.detail !== "等待接力") return;
      node.detail = {
        done: "已完成交接",
        working: "正在工作",
        attention: "需要处理",
        pending: "等待接力",
      }[node.status];
    });

    return {
      nodes,
      progress: Math.max(0, Math.min(100, Number(run?.progress_percent || 0))),
      status: run?.status || workspace.stage || "pending",
      message: run?.message || workspace.stageDetail || "等待研究团队开始",
      qualityGate: gate ? {
        status: gate.status || "pending",
        passed: Boolean(gate.passed),
        blocking: Boolean(gate.blocking),
        message: gate.message || "",
      } : null,
    };
  }

  function actionActivity(action, observation, tasksById) {
    const task = tasksById.get(action.research_task_id) || {};
    const actionName = String(action.action || "").toUpperCase();
    const detail = actionName === "SEARCH"
      ? action.query
      : actionName === "FETCH"
        ? action.url
        : actionName === "SUBMIT_EVIDENCE"
          ? action.supports
          : actionName === "FINISH"
            ? action.finish_status
        : actionName === "READ"
          ? "已阅读来源并定位可引用片段"
          : task.objective || "";
    return {
      key: `action:${action.id}`,
      kind: "action",
      agent: "Research Agent",
      action: actionName,
      title: ACTION_COPY[actionName] || "执行研究行动",
      detail: detail || "",
      query: actionName === "SEARCH" ? action.query || "" : "",
      sourceUrl: action.url || observation?.payload?.source_url || "",
      sourceTitle: observation?.payload?.source_title || "",
      evidenceRef: observation?.payload?.evidence_id || "",
      taskLabel: [task.competitor, task.dimension].filter(Boolean).join(" · "),
      status: observation?.status || (actionName === "FINISH" ? "completed" : "running"),
      createdAt: action.created_at || observation?.created_at || "",
      sortOrder: timestamp(action.created_at || observation?.created_at),
    };
  }

  function eventActivity(event) {
    if (event.event_type === "agent_action") {
      const data = event.data || {};
      return {
        key: `event:${event.id || event.sequence}`,
        kind: "action",
        agent: data.agent_label || "Research Agent",
        action: data.action || "ACTION",
        title: ACTION_COPY[data.action] || "执行研究行动",
        detail: data.public_summary || event.message || "",
        query: data.query || "",
        sourceUrl: data.source_url || "",
        sourceTitle: data.source_title || "",
        evidenceRef: data.evidence_id || "",
        taskLabel: [data.competitor, data.dimension].filter(Boolean).join(" · "),
        status: data.action_status || event.status || "completed",
        createdAt: data.action_created_at || event.created_at || "",
        sortOrder: timestamp(data.action_created_at || event.created_at) || Number(event.sequence || 0),
      };
    }

    const visibleTeamEvents = new Set([
      "pipeline_queued",
      "pipeline_started",
      "stage_started",
      "stage_completed",
      "stage_resumed",
      "handoff_created",
      "research_queued",
      "research_started",
      "research_task_started",
      "research_task_completed",
      "research_task_failed",
      "research_completed",
      "research_failed",
      "research_stopped",
      "pipeline_completed",
      "pipeline_failed",
      "pipeline_stopped",
      "pipeline_interrupted",
    ]);
    if (!visibleTeamEvents.has(event.event_type)) return null;
    const data = event.data || {};
    const handoff = event.event_type === "handoff_created";
    const researchTaskStarted = event.event_type === "research_task_started";
    const researchTaskCompleted = event.event_type === "research_task_completed";
    const stageAgent = handoff ? agentLabel(data.recipient) : ({
      planning: "Planner",
      researching: "Research Agent",
      analyzing: "Analyst",
      reporting: "Writer",
      completed: "Research Team",
    }[event.stage] || "Research Team");
    const failedTask = event.event_type === "research_task_failed";
    const failedPipeline = [
      "research_failed",
      "pipeline_failed",
      "pipeline_interrupted",
    ].includes(event.event_type);
    return {
      key: `event:${event.id || event.sequence}`,
      kind: handoff ? "handoff" : "status",
      agent: stageAgent,
      action: handoff
        ? "HANDOFF"
        : (researchTaskStarted || researchTaskCompleted || failedTask)
          ? "TASK"
          : "STATUS",
      title: handoff
        ? "结构化成果已交接"
        : researchTaskStarted
          ? "开始新的研究单元"
          : researchTaskCompleted
            ? "研究单元已完成"
        : failedTask
          ? "研究单元需要重试"
          : failedPipeline
            ? "团队执行需要处理"
          : event.message || "团队状态更新",
      detail: handoff
        ? `${agentLabel(data.sender)} → ${agentLabel(data.recipient)}`
        : researchTaskStarted
          ? "Research Agent 正在处理下一项信息需求。"
          : researchTaskCompleted
            ? `研究结果：${data.outcome || "已完成"}`
        : failedTask
          ? "当前研究单元未完成，团队将根据剩余预算继续调度。"
          : failedPipeline
            ? "执行详情已保留在 Developer Console，产品工作区不展示内部错误日志。"
          : event.message || "",
      query: "",
      sourceUrl: "",
      sourceTitle: "",
      evidenceRef: "",
      taskLabel: "",
      status: event.status || "running",
      createdAt: event.created_at || "",
      sortOrder: timestamp(event.created_at) || Number(event.sequence || 0),
    };
  }

  function agentLabel(value) {
    return {
      research_planner_agent: "Planner",
      research_agent_coordinator: "Research Agent",
      research_evidence_agent: "Research Agent",
      professional_research_analyst_agent: "Analyst",
      citation_agent: "Citation",
      professional_writer_agent: "Writer",
      reviewer_agent: "Reviewer",
      quality_gate_orchestrator: "QualityGate",
    }[value] || value || "Research Team";
  }

  function activities(workspace, events) {
    const tasksById = new Map(asArray(workspace.researchTasks).map((item) => [item.id, item]));
    const observationsByAction = new Map(
      asArray(workspace.researchAgentObservations).map((item) => [item.action_id, item]),
    );
    const streamedActions = new Set(
      events
        .filter((event) => event.event_type === "agent_action")
        .map((event) => event.data?.action_id)
        .filter(Boolean),
    );
    const items = events.map(eventActivity).filter(Boolean);
    asArray(workspace.researchAgentActions).forEach((action) => {
      if (!streamedActions.has(action.id)) {
        items.push(actionActivity(action, observationsByAction.get(action.id), tasksById));
      }
    });
    return items
      .sort((left, right) => left.sortOrder - right.sortOrder)
      .slice(-160);
  }

  function evidenceLibrary(workspace) {
    const sourcesById = new Map(asArray(workspace.sources).map((item) => [item.id, item]));
    const chunksById = new Map(asArray(workspace.sourceChunks).map((item) => [item.id, item]));
    const claims = asArray(workspace.claims).length
      ? asArray(workspace.claims)
      : asArray(workspace.claimsV2);
    const claimsByEvidence = new Map();
    claims.forEach((claim) => {
      asArray(claim.evidence_ids).forEach((evidenceId) => {
        const bound = claimsByEvidence.get(evidenceId) || [];
        bound.push(claim);
        claimsByEvidence.set(evidenceId, bound);
      });
    });

    return asArray(workspace.evidence).map((evidence) => {
      const source = sourcesById.get(evidence.source_id) || null;
      const chunkId = evidence.metadata?.source_chunk_id || evidence.chunk_id || "";
      const chunk = chunksById.get(chunkId) || null;
      return {
        key: evidence.id,
        title: source?.title || evidence.normalized_fact || "已验证证据",
        quote: evidence.snippet || evidence.normalized_fact || "",
        fact: evidence.normalized_fact || evidence.snippet || "",
        dimension: evidence.dimension || "",
        competitor: evidence.competitor || "",
        confidence: Number(evidence.confidence || 0),
        sourceType: source?.source_type || "",
        sourceUrl: source?.url || evidence.metadata?.source_url || "",
        sourceReliability: Number(source?.reliability_score || 0),
        source,
        chunk,
        evidence,
        claims: claimsByEvidence.get(evidence.id) || [],
      };
    });
  }

  function build({ workspace = {}, run = null, events = [] } = {}) {
    const safeEvents = asArray(events);
    const library = evidenceLibrary(workspace);
    return {
      pipeline: pipeline(workspace, run, safeEvents),
      activities: activities(workspace, safeEvents),
      evidence: library,
      evidenceByKey: new Map(library.map((item) => [item.key, item])),
    };
  }

  global.WorkspaceProjection = Object.freeze({ build, PIPELINE_ROLES });
})(window);
