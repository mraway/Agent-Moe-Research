# Theme D: Agent runtime security & task deviation

Search date: 2026-09-02. Every entry below was located in a WebSearch result and then its arXiv/ACL/GitHub/publisher page was fetched with WebFetch to confirm title, first author and year. "Verified: yes" means that fetch succeeded. Nothing here is from memory alone.

## Entries (grouped)

### (a) Benchmarks / datasets

### [1] tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains (Yao et al., 2024, arXiv)
- URL: https://arxiv.org/abs/2406.12045  (code: https://github.com/sierra-research/tau-bench)
- Verified: yes
- Summary: Customer-service agent benchmark with two domains: retail (115 tasks; 7 write + 8 read tools) and airline (50 tasks; 6 write + 7 read tools). The agent gets a markdown policy document and domain APIs; an LLM-simulated user (with identity/intent/preferences) drives the conversation. Reward is binary: final DB state must equal the annotated goal DB and required info must appear in agent outputs (r = r_action x r_output); pass^k measures consistency. GPT-4o succeeds on <50% of tasks, pass^8 <25% in retail.
- Relevance to our hypothesis: The natural scenario base. Its benign task set defines the "normal routing profile"; its policy doc gives explicit scope so that "deviation" (writing code, off-topic help, policy-violating refunds) is well-defined; its DB-diff grader gives a deterministic label for whether an attack changed the agent's actions.
- Rating: High

### [2] tau2-bench: Evaluating Conversational Agents in a Dual-Control Environment (Barres et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2506.07982  (code: https://github.com/sierra-research/tau2-bench, MIT)
- Verified: yes
- Summary: Successor to [1]: retail 115, airline 50, plus telecom 114 tasks (2,285 generatable) where both user and agent can call tools (Dec-POMDP "dual control"). Five evaluation criteria: DB check, status assertions, natural-language assertions, communication-info check, action matching. Repo now also lists mock and banking_knowledge domains and voice/full-duplex evaluation. User personas are "easy/hard" only; the paper explicitly contains no adversarial or off-policy user simulation.
- Relevance to our hypothesis: Best maintained scenario base (policies, tools, user simulator, graders all open). We must add the adversarial user / poisoned tool-result layer ourselves; the action-matching and NL-assertion graders can be reused to label success of deviation.
- Rating: High

### [3] AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents (Debenedetti et al., 2024, NeurIPS 2024 Datasets & Benchmarks)
- URL: https://arxiv.org/abs/2406.13352  (https://github.com/ethz-spylab/agentdojo)
- Verified: yes
- Summary: 97 realistic user tasks (workspace/email, banking, travel, Slack) and 629 security test cases where injections in tool outputs try to make the agent perform an "injection task" (e.g., send money, exfiltrate data). Utility and attack success are checked deterministically against environment state. Extensible attack/defense registry (tool filter, spotlighting/delimiting, PI-detector, repeat-prompt); many later defenses (CaMeL, MELON, Progent, AgentArmor, LlamaFirewall) report on it.
- Relevance to our hypothesis: De-facto standard for indirect-injection-via-tool-result evaluation; its banking suite is close to a customer-service agent. Reuse its injection-task pattern and deterministic ground truth; we can port its attack templates into the tau-bench tool outputs.
- Rating: High

### [4] InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated Large Language Model Agents (Zhan et al., 2024, ACL Findings)
- URL: https://arxiv.org/abs/2403.02691  (https://github.com/uiuc-kang-lab/InjecAgent)
- Verified: yes
- Summary: 1,054 test cases, 17 user tools x 62 attacker tools; two attacker intents: direct harm and data exfiltration; optional "enhanced" setting adds a hacking prompt. ReAct GPT-4 ASR 24% (roughly doubled in enhanced setting); 30 agents evaluated. Success is judged by whether the attacker tool is called.
- Relevance to our hypothesis: Cheap, single-step source of injected-tool-output attacks with clear success labels (attacker tool invoked). Good for a first "indirect injection" split of our attack taxonomy.
- Rating: Medium

### [5] Benchmarking and Defending Against Indirect Prompt Injection Attacks on Large Language Models (BIPIA) (Yi et al., 2023; KDD 2025)
- URL: https://arxiv.org/abs/2312.14197  (https://github.com/microsoft/BIPIA)
- Verified: yes
- Summary: First IPI benchmark: email QA, web QA, table QA, summarization, code QA with injected instructions; finds LLMs cannot separate informational context from actionable instructions. Proposes black-box (boundary awareness, explicit reminder) and white-box (adversarial fine-tuning) defenses that drive ASR to near zero.
- Relevance to our hypothesis: Non-agentic but useful for the "instruction in retrieved content" deviation type and for its explicit-reminder/boundary defenses as prompt-level baselines.
- Rating: Medium

### [6] WASP: Benchmarking Web Agent Security Against Prompt Injection Attacks (Evtimov et al., 2025, NeurIPS 2025 D&B)
- URL: https://arxiv.org/abs/2504.18575  (https://github.com/facebookresearch/wasp)
- Verified: yes
- Summary: End-to-end sandboxed web environment (VisualWebArena based) where attackers may only edit user-controllable content (comments, posts). Attacks partially succeed in up to 86% of cases but attacker-task completion is only 0-17% ("security by incompetence").
- Relevance to our hypothesis: Introduces the useful distinction between "agent deviated (started the attacker task)" and "attacker goal completed"; our routing signal should be evaluated against the former, earlier event.
- Rating: Medium

### [7] AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents (Andriushchenko et al., 2024, ICLR 2025)
- URL: https://arxiv.org/abs/2410.09024
- Verified: yes
- Summary: 110 explicitly malicious multi-step agent tasks (440 with augmentations), 11 harm categories, with benign counterparts. Grading combines refusal detection with rubric-based checks that the jailbroken agent still completes the harmful multi-step task.
- Relevance to our hypothesis: Source of direct-request misuse tasks and a paired benign/harmful design we should copy (same tools, different intent) so routing differences are not confounded by tool set.
- Rating: Medium

### [8] Agent Security Bench (ASB): Formalizing and Benchmarking Attacks and Defenses in LLM-based Agents (Zhang, Hanrong et al., 2024, ICLR 2025)
- URL: https://arxiv.org/abs/2410.02644  (https://github.com/agiresearch/ASB)
- Verified: yes
- Summary: 10 scenarios (e-commerce, finance, ...), 10 agents, 400+ tools, 27 attack/defense methods, 7 metrics across 13 backbones: direct prompt injection, observation (tool-result) injection, memory poisoning, Plan-of-Thought backdoor, mixed attacks; highest average ASR 84.3%, defenses weak.
- Relevance to our hypothesis: Provides a formal attack taxonomy by injection point (system prompt / user prompt / tool observation / memory) that maps onto our "where the deviation enters" axis. Bhagwatkar et al. [24] later documented metric and implementation bugs, so use with their fixes.
- Rating: Medium

### [9] Agent-SafetyBench: Evaluating the Safety of LLM Agents (Zhang, Zhexin et al., 2024, arXiv)
- URL: https://arxiv.org/abs/2412.14470  (https://github.com/thu-coai/Agent-SafetyBench)
- Verified: yes
- Summary: 349 interaction environments, 2,000 test cases, 8 risk categories, 10 failure modes; 16 agents all score <60% safety; concludes defense prompts alone are insufficient.
- Relevance to our hypothesis: Failure-mode list (e.g., following unsafe user instructions, ignoring constraints) is a ready-made deviation taxonomy at the behavioral level.
- Rating: Medium

### [10] OpenAgentSafety: A Comprehensive Framework for Evaluating Real-World AI Agent Safety (Vijayvargiya et al., 2025, ICLR 2026)
- URL: https://arxiv.org/abs/2507.06134  (https://github.com/Open-Agent-Safety/OpenAgentSafety)
- Verified: yes
- Summary: 350+ multi-turn, multi-user tasks on real tools (browser, code, files, shell, messaging) built on TheAgentCompany; 8 risk categories including adversarial/conflicting user instructions. Unsafe behavior in 51.2% (Claude 3.7 Sonnet) to 72.7% (o3-mini) of vulnerable tasks. Judging combines rule-based checks with LLM-as-judge.
- Relevance to our hypothesis: Template for a hybrid (rules + LLM judge) success labeler and for "socially pressured" multi-user deviation cases.
- Rating: Medium

### [11] LLMail-Inject: A Dataset from a Realistic Adaptive Prompt Injection Challenge (Abdelnabi et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2506.09956  (https://github.com/microsoft/llmail-inject-challenge-analysis)
- Verified: yes
- Summary: 208,095 unique attack submissions from 839 participants against an email-assistant agent; four defenses deployed live: Spotlighting, Prompt Shield (black-box classifier), LLM-judge, and TaskTracker (activation-delta probe); models Phi-3-medium and GPT-4o-mini. Success required the injected email to be retrieved, the defense evaded, and send_email called with exact arguments (destination=contact@contact.com, content=confirmation); level 4 also requires exfiltrating a specific figure.
- Relevance to our hypothesis: The largest adaptive attack corpus; and its exact-tool-call success criterion is the cleanest "deviation succeeded" label. Also the only public head-to-head of an internal-state monitor (TaskTracker) vs. classifier vs. LLM judge, i.e. the baseline lineup we need.
- Rating: High

### [12] How Vulnerable Are AI Agents to Indirect Prompt Injections? Insights from a Large-Scale Public Competition (Dziemian et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2603.15714
- Verified: yes
- Summary: 464 participants, 272k attack attempts, 13 frontier models, 41 scenarios across tool-calling, coding and computer-use agents; 8,648 successful attacks. Success = harmful action executed AND no clue of compromise in the user-facing reply. Per-model ASR 0.5% (Claude Opus 4.5) to 8.5% (Gemini 2.5 Pro); universal attacks transfer across 21/41 behaviors.
- Relevance to our hypothesis: Realistic, stealthy attacks (no visible trace in output) are exactly the case where an output-level judge fails and an internal routing signal could add value.
- Rating: Medium

### [13] Language Model Agents Under Attack: A Cross Model-Benchmark of Profit-Seeking Behaviors in Customer Service (Zhang, Jingyu, 2025, arXiv)
- URL: https://arxiv.org/abs/2512.24415
- Verified: yes
- Summary: Direct prompt injection against customer-service agents in 10 domains (car dealership, bank, online retail, healthcare portal, airline, electronics retail, second-hand marketplace, housing, hotel front desk, games). 100 attack scripts in five technique families: PI1 role-play/authority override, PI2 obfuscation, PI3 payload splitting, PI4 adversarial suffix/pressure, PI5 instruction manipulation ("ignore previous"). Five models (GPT-5, GPT-4o, Claude Opus 4.1, Gemini 2.5 Pro, DeepSeek v3.2). Success judged by two LLM judges (GPT-5 and Claude Opus 4.1) on a 0-5 rubric, success = score >= 4. Airline ~56% success, payload splitting most effective (26.2%), DeepSeek most vulnerable (26.5%), Claude least (6.5%). Data and code released.
- Relevance to our hypothesis: Closest existing thing to our "user talks the customer-service agent into a concession" setting; its five technique families are a ready direct-hijack taxonomy and its dual-LLM rubric is a reusable success judge. Airline being most vulnerable aligns with tau-bench airline as scenario.
- Rating: High

### [14] It's a TRAP! Task-Redirecting Agent Persuasion Benchmark for Web Agents (Korgul et al., 2026, arXiv, submitted Dec 2025)
- URL: https://arxiv.org/abs/2512.23128
- Verified: yes
- Summary: Social-engineering injections embedded in interface elements of high-fidelity website clones (email, professional networking), operationalizing Cialdini persuasion principles (authority, scarcity, social proof, ...). Agents get redirected in 25% of tasks on average (13% GPT-5, 43% DeepSeek-R1); small UI/context tweaks often double success.
- Relevance to our hypothesis: Provides the "social engineering" branch of our taxonomy with controlled persuasion variables; can be re-instantiated as persuasion text inside tau-bench user turns or tool results.
- Rating: Medium

### [15] FraudBench: Stress-Testing Policy-Grounded Banking Agents Against Adaptive Fraud (Pai & Xian, 2026, arXiv)
- URL: https://arxiv.org/abs/2608.18136
- Verified: yes
- Summary: Executable evaluation of a tool-using banking customer-service agent under adversarial callers: 150 authored scenarios, 107 graded (90 across ten fraud mechanisms + 17 chained adaptive attacks; 43 more held out). Each scenario specifies observable evidence, prohibited actions, safe dispositions and intervention points; asks whether the agent blocks the unsafe goal, identifies the control, intervenes before the dangerous action, and still serves a matched legitimate customer. Four agents score 49-65% attack-security.
- Relevance to our hypothesis: The most directly comparable "policy-grounded customer-service agent being socially engineered over a conversation" benchmark, with per-step intervention points that map onto a runtime interception signal. Strong candidate for a second scenario or for attack scripts.
- Rating: High

### [16] No More, No Less: Task Alignment in Terminal Agents (Mavali et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2605.12233
- Verified: yes
- Summary: Defines "task alignment" as selectively following environment instructions that serve the user goal while ignoring irrelevant/misleading/out-of-scope ones; TAB has 89 tasks derived from Terminal-Bench 2.1 with co-located useful and distractor instructions. Capable agents complete tasks yet still execute irrelevant side objectives; injection defenses that kill distractors also remove needed cues.
- Relevance to our hypothesis: Gives a precise definition of the phenomenon we call "task deviation" that is broader than security (scope creep), and shows deviation and task completion are separable, which is what a routing detector must also separate.
- Rating: Medium

### [17] Action-Based Conversations Dataset (ABCD) (Chen, Derek et al., 2021, NAACL)
- URL: https://arxiv.org/abs/2104.00783  (https://github.com/asappresearch/abcd)
- Verified: yes
- Summary: 10k+ human-human customer-support dialogues, 55 intents, agents follow multi-step, policy-constrained action sequences; tasks: Action State Tracking and Cascading Dialogue Success.
- Relevance to our hypothesis: Source of realistic benign customer intents and policy-driven action sequences for building a broad "normal" routing distribution; no tool execution, so must be paired with [1]/[2].
- Rating: Medium

### [18] Bitext Customer Support LLM Chatbot Training Dataset (Bitext, Hugging Face)
- URL: https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset
- Verified: yes
- Summary: 26,872 synthetic instruction/response pairs, 27 intents in 10 categories (Account, Cancellation Fee, Delivery, Feedback, Invoice, Newsletter, Order, Payment, Refund, Shipping Address); fields flags/instruction/category/intent/response; CDLA-Sharing-1.0; no tool calls.
- Relevance to our hypothesis: Cheap in-scope negative set for an off-topic detector and for estimating per-intent routing variance within "normal" traffic.
- Rating: Low-Medium

### (b) Defenses / runtime monitors

### [19] Get my drift? Catching LLM Task Drift with Activation Deltas (TaskTracker) (Abdelnabi et al., 2024, IEEE SaTML 2025)
- URL: https://arxiv.org/abs/2406.00799  (https://github.com/microsoft/TaskTracker)
- Verified: yes
- Summary: Defines "task drift" (external data causing deviation from the user's instruction) and detects it with linear probes / metric learning on the difference between residual-stream activations before and after the model reads external data. 500k+ instance dataset over six LLMs; near-perfect ROC-AUC out of distribution; generalizes to injections, jailbreaks and malicious instructions without training on them; no model changes.
- Relevance to our hypothesis: The closest prior work: an internal-state, inference-time drift detector. Our MoE-routing signal is a different (cheaper, natively logged) internal statistic; TaskTracker is the primary baseline and its "task drift" definition should be adopted and extended to multi-turn agents (it was evaluated on single-turn RAG-style prompts).
- Rating: High

### [20] Defending Against Indirect Prompt Injection Attacks With Spotlighting (Hines et al., 2024, arXiv)
- URL: https://arxiv.org/abs/2403.14720
- Verified: yes
- Summary: Prompt-engineering family (delimiting, datamarking, encoding) that marks untrusted data so the model does not follow instructions in it; reduces IPI ASR from >50% to <2% on GPT-family models with negligible utility loss.
- Relevance to our hypothesis: Cheapest prevention baseline; also useful as a control: does spotlighting change the routing profile of benign turns?
- Rating: Medium

### [21] StruQ / SecAlign (Chen, Sizhe et al., 2024; USENIX Security 2025 / ACM CCS 2025)
- URL: https://arxiv.org/abs/2402.06363 (StruQ), https://arxiv.org/abs/2410.05451 (SecAlign)
- Verified: yes
- Summary: Training-time defenses. StruQ separates prompt and data with a secure front-end plus fine-tuning to ignore instructions in data. SecAlign adds preference optimization on simulated injections (desired vs. injected responses), pushing ASR <10% even for stronger attacks than seen in training.
- Relevance to our hypothesis: Represent the "fix the model" alternative; relevant mainly as a baseline showing residual attacks still slip through, which a runtime monitor must catch.
- Rating: Medium

### [22] Defeating Prompt Injections by Design (CaMeL) (Debenedetti et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2503.18813  (https://github.com/google-research/camel-prompt-injection)
- Verified: yes
- Summary: Architectural defense: a privileged LLM writes a program from the trusted user query, a quarantined LLM handles untrusted data, and a custom interpreter enforces capability/data-flow policies on every tool call. On AgentDojo solves 77% of tasks with provable security vs. 84% undefended.
- Relevance to our hypothesis: Upper-bound "secure by construction" baseline; heavyweight and requires re-architecting the agent, which is what a passive routing monitor avoids.
- Rating: Medium

### [23] MELON: Provable Defense Against Indirect Prompt Injection Attacks in AI Agents (Zhu et al., 2025, ICML 2025)
- URL: https://arxiv.org/abs/2502.05174  (https://github.com/kaijiezhu11/MELON)
- Verified: yes
- Summary: Runtime detector: re-executes the trajectory with the user prompt masked and flags an attack if the agent still issues the same tool call (i.e., the action no longer depends on the user's task). Outperforms prior defenses on AgentDojo in both ASR and utility.
- Relevance to our hypothesis: Same underlying idea as ours (action became independent of the intended task) but requires a full second forward pass per step; a direct efficiency and accuracy baseline for a routing-based detector.
- Rating: High

### [24] Indirect Prompt Injections: Are Firewalls All You Need, or Stronger Benchmarks? (Bhagwatkar et al., 2025, NeurIPS 2025)
- URL: https://arxiv.org/abs/2510.05244
- Verified: yes
- Summary: Tool-input firewall (Minimizer) + tool-output firewall (Sanitizer) at the agent-tool interface reach near-perfect security with high utility on AgentDojo, ASB, InjecAgent and tau-bench; the paper then shows these benchmarks have flawed success metrics, implementation bugs and weak attacks, releases fixes, and argues for adaptive attacks.
- Relevance to our hypothesis: (i) Confirms a tau-bench prompt-injection setup already exists and can be reused; (ii) warns that evaluations must include adaptive attacks and corrected metrics; (iii) firewalls are a strong, cheap text-level baseline.
- Rating: High

### [25] LlamaFirewall: An open source guardrail system for building secure AI agents (Chennabasappa et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2505.03574  (https://github.com/meta-llama/PurpleLlama)
- Verified: yes
- Summary: Layered runtime guardrail: PromptGuard 2 (BERT-style jailbreak/injection classifier on user prompts and tool data), AlignmentCheck (LLM chain-of-thought auditor that inspects agent reasoning for goal misalignment / hijack), CodeShield (static analysis). Paper reports the combination cuts ASR to about 1.75% on AgentDojo with ~6% utility loss.
- Relevance to our hypothesis: AlignmentCheck is literally an LLM-judge "goal misalignment" monitor over trajectories, so it is the mandatory LLM-monitor baseline; PromptGuard 2 is the mandatory classifier baseline.
- Rating: High

### [26] GuardAgent: Safeguard LLM Agents by a Guard Agent via Knowledge-Enabled Reasoning (Xiang et al., 2024, ICML 2025)
- URL: https://arxiv.org/abs/2406.09187
- Verified: yes
- Summary: A second LLM agent turns natural-language safety requirements into executable guardrail code that checks target-agent actions; >98% guard accuracy on EICU-AC (healthcare access control) and 83% on Mind2Web-SC (web safety rules).
- Relevance to our hypothesis: Representative "guard-agent" baseline (policy checking on the action stream); expensive per step compared to routing statistics.
- Rating: Medium

### [27] ShieldAgent: Shielding Agents via Verifiable Safety Policy Reasoning (Chen, Zhaorun et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2503.22738
- Verified: yes
- Summary: Extracts verifiable rules from policy documents into probabilistic rule circuits, then formally checks each action; ShieldAgent-Bench has 3k instruction-trajectory pairs over 6 web environments and 7 risk categories; +11.3% over prior guardrails, 90.1% recall, 64.7% fewer API queries.
- Relevance to our hypothesis: Policy-document-to-rules pipeline fits tau-bench's policy markdown; a natural symbolic baseline for "policy-violating action" detection.
- Rating: Medium

### [28] Progent: Securing AI Agents with Privilege Control (Shi, Tianneng et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2504.11703
- Verified: yes
- Summary: Least-privilege policies as symbolic rules over tool names/arguments, LLM-generated from the user task, with SMT-checked monotonic narrowing at runtime; evaluated on AgentDojo and ASB, integrated with LangChain and OpenAI Agents SDK; large ASR reductions with utility preserved.
- Relevance to our hypothesis: Complementary enforcement layer; our detector could trigger Progent-style privilege narrowing when routing drift is detected.
- Rating: Medium

### [29] AgentArmor: Enforcing Program Analysis on Agent Runtime Trace to Defend Against Prompt Injection (Wang, Peiran et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2508.01249
- Verified: yes
- Summary: Treats the runtime trace as a program (CFG/DFG/PDG), attaches property metadata and runs type-based security checks; on AgentDojo ASR drops to 3% with 1% utility loss.
- Relevance to our hypothesis: Trace-level (not model-internal) runtime monitor baseline.
- Rating: Medium

### [30] PrefixGuard: From LLM-Agent Traces to Online Failure-Warning Monitors (Huang, Xinmiao et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2605.06455
- Verified: yes
- Summary: Learns typed-step adapters from traces and trains supervised prefix-risk monitors that emit an online risk score per step with no LLM inference at deployment; evaluated on WebArena, tau2-bench, SkillsBench, TerminalBench with peak AUPRC 0.900/0.710/0.533/0.557 (+0.137 over raw-text baselines).
- Relevance to our hypothesis: Shows that cheap, non-LLM step-level monitors on tau2-bench are viable and gives a reference AUPRC on tau2-bench; a direct "cheap external signal" baseline against our "cheap internal signal".
- Rating: High

### [31] Real-Time Detection and Repair of LLM Agent Failures (Dubey, 2026, arXiv)
- URL: https://arxiv.org/abs/2608.02464
- Verified: yes
- Summary: One-class echo-state-network ensemble + CUSUM change-point alarms over step features to catch looping, tool errors, goal drift and fabrication mid-episode (~200 us/step); 2,823 episodes, AUROC 0.872, 0.71 recall at 5% FAR; rollback repair recovers 45% of failures.
- Relevance to our hypothesis: Establishes the "sequential change-point detection on a per-step signal" evaluation protocol (recall at fixed FAR, latency) we should adopt for routing trajectories.
- Rating: Medium

### [32] AI Control: Improving Safety Despite Intentional Subversion (Greenblatt et al., 2023, ICML 2024)
- URL: https://arxiv.org/abs/2312.06942
- Verified: yes
- Summary: Introduces control protocols (trusted monitoring, trusted editing, untrusted monitoring with collusion countermeasures) where a weaker trusted model (GPT-3.5) monitors a stronger untrusted one (GPT-4) on code tasks; large safety gains over baselines.
- Relevance to our hypothesis: Conceptual frame for the "trusted monitor" baseline (small LLM judge over the trajectory) and for measuring safety at a fixed audit budget.
- Rating: Medium

### [33] AutoMonitor-Bench: Evaluating the Reliability of LLM-Based Misbehavior Monitor (Yang, Shu et al., 2026, ACL 2026 Findings)
- URL: https://arxiv.org/abs/2601.05752
- Verified: yes
- Summary: 3,010 annotated paired misbehavior/benign samples (plus 153k training corpus) over QA, code and reasoning; metrics Miss Rate and False Alarm Rate; 22 LLM monitors show large variance and a consistent MR/FAR trade-off, and fine-tuning generalizes poorly to implicit misbehaviors.
- Relevance to our hypothesis: Evidence that LLM-judge monitors are unreliable and costly, motivating a cheaper orthogonal signal; also the MR/FAR reporting convention.
- Rating: Medium

### [34] Hodoscope: Unsupervised Monitoring for AI Misbehaviors (Zhong et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2604.11072  (https://github.com/AR-FORUM/hodoscope)
- Verified: yes
- Summary: Summarize actions, embed, then diff behavior distributions between agent groups (kernel density) to surface distinctive, suspicious patterns for human review; found an unknown Commit0 exploit and cut review effort 6-23x.
- Relevance to our hypothesis: Methodological twin of our idea at the behavior level (distribution differences as the monitor signal); we do the same in expert-routing space.
- Rating: Medium

### (c) Off-topic / scope-enforcement guardrails

### [35] A Flexible Large Language Models Guardrail Development Methodology Applied to Off-Topic Prompt Detection (Chua et al., 2024, arXiv; GovTech Singapore)
- URL: https://arxiv.org/abs/2411.12946
- Verified: yes
- Summary: Frames off-topic misuse as "is the user prompt relevant to the system prompt?"; data-free methodology generates a synthetic (system prompt, user prompt, label) dataset with an LLM, trains off-topic classifiers that beat heuristics and generalize to jailbreak/harmful prompts; dataset and models open-sourced.
- Relevance to our hypothesis: The canonical off-topic guardrail baseline and a ready off-topic prompt set (e.g. "write me code" to a support bot) for the misuse branch of our taxonomy.
- Rating: High

### [36] kNNGuard: Turning LLM Hidden Activations into a Training-Free Configurable Guardrail (Abdelfattah et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2607.02072
- Verified: yes
- Summary: Multi-layer kNN over hidden activations plus embedding scores, using only ~50 labeled safe/unsafe prompts per domain; detects unsafe, off-topic and adversarial prompts across six domains with F1 competitive with fine-tuned guardrails, 2.7x faster than the best comparable guardrail, domain re-targeting in <10 s.
- Relevance to our hypothesis: The closest internal-signal off-topic detector (dense activations rather than sparse routing); a must-have baseline and a natural ablation (activations vs. expert-selection vectors).
- Rating: High

### [37] NeMo Guardrails: A Toolkit for Controllable and Safe LLM Applications with Programmable Rails (Rebedea et al., 2023, EMNLP 2023 Demo)
- URL: https://aclanthology.org/2023.emnlp-demo.40/  (https://github.com/NVIDIA-NeMo/Guardrails)
- Verified: yes
- Summary: Programmable topical/dialogue rails (Colang) that keep a bot on allowed topics and dialogue flows via canonical-form classification and flow matching; also execution rails for fact-checking/moderation.
- Relevance to our hypothesis: Deployed-system baseline for scope enforcement on the user turn; useful for showing the gap when deviation arrives via tool results, which topical rails do not inspect.
- Rating: Medium

### [38] A Closer Look at System Prompt Robustness (RealGuardrails) (Mu et al., 2025, arXiv)
- URL: https://arxiv.org/abs/2502.12197
- Verified: yes
- Summary: Builds evaluation/fine-tuning data from real system prompts (GPT Store, HuggingChat) with guardrails that users try to override; models often forget relevant guardrails or fail to resolve system-user conflicts; fine-tuning and classifier-free guidance help but "current techniques fall short".
- Relevance to our hypothesis: Provides realistic system-prompt scope rules and user override attempts to build the "direct hijack / scope violation" split without tools.
- Rating: Medium

### (d) Taxonomies / definitions / incidents

### [39] Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection (Greshake et al., 2023, AISec@CCS 2023)
- URL: https://arxiv.org/abs/2302.12173
- Verified: yes
- Summary: Coins indirect prompt injection; taxonomy of injection channels (passive, active, user-driven, hidden) and impacts: information gathering/data theft, fraud, intrusion (API control, code execution), malware/worming, manipulated content, availability. Demonstrated on Bing Chat and code-completion engines.
- Relevance to our hypothesis: Source taxonomy for the "indirect" branch; its "manipulated content / functionality manipulation" categories are the CS-agent deviation types.
- Rating: High

### [40] OWASP Top 10 for Agentic Applications for 2026 (OWASP GenAI Security Project, Dec 9 2025)
- URL: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/  (secondary: https://adversa.ai/blog/asi01-agent-goal-hijack-a-practical-security-guide/, https://docs.modulos.ai/frameworks/owasp-top-10-agentic)
- Verified: yes (official page confirms title/date; risk list read from secondary summaries)
- Summary: ASI01 "Agent Goal Hijack" = manipulation of an agent's stated or inferred objectives so it pursues actions diverging from the user's original intent; sub-classified by trust boundary crossed (user input, retrieved content, tool/API response, inter-agent, configuration). ASI02 tool misuse, ASI06 memory poisoning, ASI10 rogue agent (operating outside policy through drift or compromise), etc.
- Relevance to our hypothesis: Industry-standard vocabulary; our four deviation classes map to ASI01 boundaries (user input = direct hijack/social engineering; tool response = indirect injection) plus ASI10-style scope drift.
- Rating: High

### [41] Taxonomy of Failure Modes in Agentic AI Systems v2.0 (Microsoft AI Red Team, June 2026)
- URL: https://www.microsoft.com/en-us/security/blog/2026/06/04/updating-taxonomy-failure-modes-agentic-ai-systems-year-red-teaming-taught-us/  (PDF: https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/bade/documents/products-and-services/en-us/security/Taxonomy-of-Failure-Modes-in-Agentic-AI-Systems-v2-0.pdf)
- Verified: yes
- Summary: Grounded in a year of red-teaming deployed agents; v1 (Apr 2025) modes: agent compromise, injection, impersonation, flow manipulation, memory poisoning, cross-domain prompt injection, HitL bypass; v2 adds goal hijacking (instructions that look task-aligned but silently redirect the terminal goal), session context contamination, MCP/plugin abuse, supply-chain, CUA visual attacks, inter-agent trust escalation, capability disclosure.
- Relevance to our hypothesis: "Goal hijacking that appears aligned with the task" and "session context contamination" are the hard cases for text-level detectors and the interesting cases for routing-based detection.
- Rating: Medium

### [42] Chevrolet of Watsonville chatbot incident (VentureBeat, Masse, Dec 19 2023)
- URL: https://venturebeat.com/ai/a-chevy-for-1-car-dealer-chatbots-show-perils-of-ai-for-customer-service
- Verified: yes
- Summary: Fullpath's ChatGPT-based dealership assistant was made to write a Python script for Navier-Stokes fluid-flow equations (Chris White), to append "that's a legally binding offer - no takesies backsies" and accept $1 for a 2024 Tahoe (Chris Bakke), and to recommend competitor brands; dealerships disabled the bots.
- Relevance to our hypothesis: The canonical motivating incident: exactly our three misuse classes (off-topic code generation, social-engineered concession, instruction override) on a customer-service bot.
- Rating: High

### [43] Moffatt v. Air Canada, 2024 BCCRT 149 (BC Civil Resolution Tribunal, Feb 14 2024)
- URL: https://www.mccarthy.ca/en/insights/blogs/techlex/moffatt-v-air-canada-misrepresentation-ai-chatbot
- Verified: yes
- Summary: Tribunal held Air Canada liable for negligent misrepresentation by its website chatbot about bereavement fares, rejecting the claim that the chatbot is a separate legal entity ("makes no difference whether the information comes from a static page or a chatbot").
- Relevance to our hypothesis: Establishes real liability for off-policy chatbot outputs, motivating runtime interception in the airline domain.
- Rating: Medium

### [44] Taxonomy and Consistency Analysis of Safety Benchmarks for AI Agents (Li, Miles Q. et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2605.16282
- Verified: yes
- Summary: Six-axis taxonomy over 40 agent-safety benchmarks (2023-2026); finds no ranking concordance across benchmarks (Kendall W = 0.10), metric fragmentation, mostly externally imposed risks, and almost no robustness evaluation.
- Relevance to our hypothesis: Guidance on choosing benchmarks and reporting; supports evaluating on more than one attack source and reporting both miss and false-alarm rates.
- Rating: Medium

### (e) Adjacent: MoE routing as a security signal (verified, for the open-gap section)

### [45] RouteScan: A Non-Intrusive Approach to Auditing MoE LLMs Safety via Expert Routing Telemetry (Lv et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2605.24817
- Verified: yes
- Summary: Uses GPU-thread telemetry of expert execution during prefill as a proxy for routing to detect harmful prompts; AUROC >0.91 on unseen harmful domains across four MoE LLMs, without access to prompts or hidden states.
- Relevance to our hypothesis: Direct evidence that routing-derived signals carry safety information; limited to single-prompt harmfulness during prefill, not multi-turn agent task deviation.
- Rating: High

### [46] RouteHijack: Routing-Aware Attack on Mixture-of-Experts LLMs (Xu, Zhiyuan et al., 2026, arXiv) and RASA: Routing-Aware Safety Alignment for MoE Models (Liang, Jiacheng et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2605.02946 ; https://arxiv.org/abs/2602.04448
- Verified: yes
- Summary: RouteHijack finds safety behavior concentrated in few experts and optimizes suffixes that suppress them (69.3% ASR across seven MoE LLMs). RASA identifies experts over-activated during jailbreaks and repairs them with fixed routing plus routing-consistency regularization.
- Relevance to our hypothesis: Shows routing is both a signal and an attack surface: an adaptive attacker can shape routing, so our detector must be evaluated against routing-aware adaptive attacks.
- Rating: Medium

### Additional verified references (brief)
- The Instruction Hierarchy (Wallace et al., 2024, OpenAI) https://arxiv.org/abs/2404.13208 - training-time privilege ordering system > user > tool; baseline for "model already trained to resist".
- IsolateGPT (Wu et al., 2024, NDSS 2025) https://arxiv.org/abs/2403.04960 - execution isolation for LLM apps, <30% overhead on 75% of queries.
- AgentSpec (Wang, Haoyu et al., 2025) https://arxiv.org/abs/2503.18666 - DSL for runtime rules (trigger/predicate/enforcement); >90% unsafe code prevented; ms overhead.
- Lessons from Defending Gemini Against Indirect Prompt Injections (Shi et al., 2025) https://arxiv.org/abs/2505.14534 - adaptive attacks beat static defenses in 16/24 cases; continuous ART + fine-tuning.
- Agent Data Injection Attacks are Realistic Threats to AI Agents (Choi et al., 2026) https://arxiv.org/abs/2607.05120 - injection disguised as trusted metadata/tool-format data; defeats instruction/data-separation defenses on Claude Code, Codex, Gemini CLI.
- AI Agents May Always Fall for Prompt Injections (Abdelnabi & Bagdasarian, 2026) https://arxiv.org/abs/2605.17634 - contextual-integrity impossibility argument for text-level defenses.
- MultiWOZ (Budzianowski et al., 2018, EMNLP) https://arxiv.org/abs/1810.00278 - 10k multi-domain task-oriented dialogues; low relevance (no tools, no policies).
- Microsoft TaskTracker toolkit https://github.com/microsoft/TaskTracker - released activations + probes.

## Key takeaways for our project

- Scenario base: use tau2-bench (retail 115 / airline 50 / telecom 114 tasks; open policy docs, tools, user simulator, five graders, MIT) as the "normal" workload. Airline is the most attack-prone domain in the only customer-service injection benchmark [13], and both the Chevrolet [42] and Air Canada [43] incidents are sales/airline support, so airline + retail is a defensible choice. Bhagwatkar et al. [24] already run injection attacks on tau-bench, so a tau-bench IPI harness exists to adapt.
- Attack taxonomy (four classes, each with an existing source): (1) direct hijack / instruction override -> the five PI families of [13] (role-play authority, obfuscation, payload splitting, pressure suffix, "ignore previous") plus RealGuardrails overrides [38]; (2) social engineering over multiple turns -> FraudBench chained adaptive attacks [15] and TRAP persuasion principles [14]; (3) indirect injection via tool results -> AgentDojo injection tasks [3], InjecAgent attacker tools [4], LLMail-Inject payloads [11], and ADI-style disguised metadata [Choi 2026]; (4) off-topic misuse (homework, code writing) -> GovTech off-topic dataset [35] and the Watsonville prompts [42]. Label each by OWASP ASI01 trust boundary (user input vs tool/API response) [40].
- Definition of "deviation": adopt TaskTracker's "task drift" [19] (external data causes departure from the user/operator instruction) generalized to multi-turn agents, and Mavali et al.'s "task alignment" [16] for the scope-creep case; distinguish "agent started the attacker/off-scope task" from "attacker goal completed" as WASP does [6].
- Success judges (what prior work uses): deterministic state/tool-call checks wherever possible (AgentDojo env checks [3], InjecAgent attacker-tool call [4], LLMail-Inject exact send_email args [11], tau-bench DB diff and action matching [1][2]); LLM rubric judges only where needed, ideally two independent judges with a threshold (GPT-5 + Claude 0-5 rubric, success >= 4 in [13]; conservative agreement in [12]); report MR/FAR as in AutoMonitor-Bench [33]. Beware that judges themselves are injectable [24 discussion; judge-injection literature].
- Baselines to compare against, by cost tier: (i) text classifiers on the incoming turn/tool output: PromptGuard 2 [25], Prompt Shield [11], GovTech off-topic classifier [35]; (ii) internal-activation probes: TaskTracker [19] (primary), kNNGuard [36]; (iii) LLM-judge trajectory monitors: LlamaFirewall AlignmentCheck [25], an AI-Control-style trusted monitor [32]; (iv) re-execution / trace analysis: MELON [23], AgentArmor [29]; (v) cheap external-trace monitors: PrefixGuard [30] (has tau2-bench AUPRC 0.710), ESN+CUSUM [31]; (vi) prevention baselines for context: Spotlighting [20], tool firewalls [24], CaMeL [22], Progent [28].
- Evaluation protocol: per-step online detection with recall at fixed FAR and detection latency (steps before the deviating tool call), following [30][31]; separate utility on benign tau2 tasks (false alarms on legitimate but unusual requests, e.g. "hard" personas).
- Adaptive attacks are mandatory: static benchmarks saturate [24]; Gemini defenses fell to adaptive attacks in 16/24 cases; RouteHijack [46] shows routing can be adversarially steered, so include a routing-aware adaptive attacker.
- Data for the "normal" distribution: tau2 benign trajectories; enlarge intent coverage with ABCD intents [17] and Bitext intents [18] to avoid flagging rare-but-legitimate requests.

## Open gap

No prior work uses MoE expert-routing trajectories as a runtime deviation signal for agents. The nearest internal-state detectors (TaskTracker [19], kNNGuard [36]) use dense residual activations and were evaluated on single-turn prompts, not multi-turn tool-using agents; the nearest routing-based work (RouteScan [45]) detects harmful single prompts from prefill telemetry, not task drift across an agent trajectory. Existing customer-service benchmarks (tau/tau2 [1][2]) contain no adversarial users, and existing agent-security benchmarks (AgentDojo, ASB, InjecAgent) are not customer-service scenarios and have known metric weaknesses [24]; the only customer-service attack benchmark [13] covers direct injection with a single-turn LLM-judged outcome, and FraudBench [15] covers social engineering in banking only. So the concrete gaps are: (1) a tau2-based customer-service testbed that unifies direct hijack, multi-turn social engineering, indirect tool-result injection and off-topic misuse with deterministic deviation labels; (2) a comparison of routing-trajectory detectors against activation probes, text classifiers and LLM-judge monitors under a common recall-at-FAR/latency protocol; and (3) robustness of routing-based detection to routing-aware adaptive attacks [46].
