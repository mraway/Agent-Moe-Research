# Theme C: Activation-based monitoring

Scope: dense-model latent-space / activation / attention monitors for safety and behaviour detection, which a MoE-routing-based task-drift monitor must be compared against and can borrow from. All entries below were located via web search and their abstract page (arXiv / ACL Anthology / official blog) was fetched to confirm title, first author and year. Search date: 2026-09-02.

## Entries

### [1] Get my drift? Catching LLM Task Drift with Activation Deltas (Abdelnabi et al., 2024; SaTML 2025)
- URL: https://arxiv.org/abs/2406.00799
- Verified: yes
- Summary: Defines "task drift" (external data causing the LLM to deviate from the user's instruction, e.g. via indirect prompt injection) and detects it from *activation deltas*: the residual-stream activation of the same probe position before vs. after the external data is processed. A simple linear classifier on the delta reaches near-perfect ROC AUC on an OOD test set and generalizes to unseen attack families (prompt injections, jailbreaks, malicious instructions) without training on any attack. Releases the TaskTracker toolkit (>500K instances, activations from six models).
- Relevance to our hypothesis: This is the direct dense-model analogue of our idea (drift = deviation of internal state from the "clean" state on the same task). Borrow the delta/contrast design (clean-prefix vs. after-data), the OOD attack-family hold-out protocol, and TaskTracker as both dataset and baseline; our routing-histogram delta is a discrete version of their activation delta.
- Rating: High

### [2] PIShield: Detecting Prompt Injection Attacks via Intrinsic LLM Features (Zou et al., 2025)
- URL: https://arxiv.org/abs/2510.14005
- Verified: yes
- Summary: Observes that instruction-tuned LLMs internally encode a distinguishable signal for inputs containing injected instructions. Trains a linear classifier on residual-stream representations (no fine-tuning, no generation) and reports consistently low FPR and FNR on short- and long-context prompt-injection benchmarks, outperforming existing detectors. Code released.
- Relevance to our hypothesis: Closest "single-forward-pass linear probe on the residual stream" baseline for the injection case; we should replicate its evaluation set and report FPR/FNR side by side with a router-logit probe.
- Rating: High

### [3] Defending against Indirect Prompt Injection by Instruction Detection (Wen et al., 2025; Findings of EMNLP 2025)
- URL: https://arxiv.org/abs/2505.06311
- Verified: yes
- Summary: InstructDetector detects indirect prompt injection by looking at *behavioural state change* in the LLM: hidden states and gradients from intermediate layers are used as features. Reports 99.60% detection accuracy in-domain and 96.90% in the generalized setting, and reduces attack success rate to 0.03% on BIPIA.
- Relevance to our hypothesis: Demonstrates that intermediate-layer internal features are highly discriminative for injected instructions; BIPIA is a benchmark we should reuse, and the "in-domain vs. generalized" split is a reporting convention to copy.
- Rating: High

### [4] Attention Tracker: Detecting Prompt Injection Attacks in LLMs (Hung et al., 2025; Findings of NAACL 2025)
- URL: https://aclanthology.org/2025.findings-naacl.123/ (arXiv: https://arxiv.org/abs/2411.00348)
- Verified: yes
- Summary: Identifies a "distraction effect": specific attention heads ("important heads") shift attention from the original instruction to the injected instruction. Attention Tracker is training-free, reads attention scores that are already produced during the original forward pass (no extra inference), and improves AUROC by up to 10.0% over existing detectors on Open-Prompt-Injection and deepset, working even on small LLMs.
- Relevance to our hypothesis: The closest methodological cousin: a *free by-product of inference* (attention maps there, router decisions here) used as an injection signal. Borrow the "important heads" selection idea (find the routing layers/experts most sensitive to instruction vs. data) and the zero-extra-inference cost argument.
- Rating: High

### [5] Latent Adversarial Detection: Adaptive Probing of LLM Activations for Multi-Turn Attack Detection (Kulkarni, 2026)
- URL: https://arxiv.org/abs/2604.28129
- Verified: yes
- Summary: Multi-turn attacks (trust-building, pivoting, escalation) leave a signature in residual-stream *trajectories*: an "adversarial restlessness" where the path length of activation movement in attack phases far exceeds benign conversations. Trajectory features lift detection from 76.2% to 93.8% on synthetic data across four model families (24B-70B); on real LMSYS-Chat-1M traffic detection is 47-71%, and 3-source training gives 89.4% detection at 2.4% FPR. Three-phase turn-level labels (benign/pivoting/adversarial) were essential; binary labels gave 50-59% FPR.
- Relevance to our hypothesis: The only entry that treats internal state as a *trajectory over turns*, which is exactly what a routing trace is. Borrow path-length / drift-over-time features and the turn-level three-phase labelling; note the large synthetic-to-real gap as a warning for our own evaluation.
- Rating: High

### [6] Simple probes can catch sleeper agents (MacDiarmid et al., 2024; Anthropic Alignment Note)
- URL: https://www.anthropic.com/research/probes-catch-sleeper-agents
- Verified: yes
- Summary: "Defection probes" are difference-in-means linear directions on residual-stream activations built from generic contrast pairs (e.g. "Are you doing something dangerous? yes/no") that require no knowledge of the trigger. They predict backdoored-model defection with AUROC >99%; even a single contrast pair gives 99.3% on the code-vulnerability sleeper agent. The signal is most salient in *middle* residual layers and is computed from prompt activations only (before generation). Null result on an unbackdoored Claude 2, i.e. the probe is specific.
- Relevance to our hypothesis: Template for cheap, trigger-agnostic probes: mean-difference direction, middle layers, prompt-only (pre-generation) detection, ROC/AUROC reporting, and an explicit "clean model" control. Our router-level analogue is a difference in expert-usage distributions between contrast pairs.
- Rating: High

### [7] Detecting Strategic Deception Using Linear Probes (Goldowsky-Dill et al., 2025; ICML 2025)
- URL: https://arxiv.org/abs/2502.03407
- Verified: yes
- Summary: Trains linear probes on Llama-3.3-70B-Instruct activations from (a) honest-vs-deceptive instruction contrasts and (b) simple roleplay, and tests generalization to realistic deception (concealing insider trading, sandbagging safety evals). AUROC 0.96-0.999; at 1% FPR on unrelated chat data, 95-99% of deceptive responses are caught. Authors conclude the performance is still insufficient as a robust defence.
- Relevance to our hypothesis: Provides the "1% FPR on benign traffic, report recall" convention, which is the right operating point for a runtime interceptor in customer service. Also motivates why internal monitors beat output monitors: outputs can look benign while intent is off-task.
- Rating: High

### [8] Detecting High-Stakes Interactions with Activation Probes (McKenzie et al., 2025; NeurIPS 2025)
- URL: https://arxiv.org/abs/2506.10805
- Verified: yes
- Summary: Trains several probe architectures (linear, attention, etc.) on synthetic data to flag "high-stakes" interactions; they generalize to diverse OOD real-world data. Probe performance is comparable to prompted or fine-tuned medium-sized LLM monitors at ~six orders of magnitude lower compute, because activations of the monitored model are reused. Shows a probe-plus-LLM cascade beats either alone at fixed compute (e.g. matching a fine-tuned Llama-3.1-8B by sending only the 40% most-uncertain cases to it) and that as few as 32 in-domain samples improve probes.
- Relevance to our hypothesis: The canonical cost-vs-accuracy comparison framework (probe vs. prompted LLM vs. fine-tuned LLM vs. cascade, at equal FLOPs). Copy the hierarchical-monitor evaluation and the "N deployment samples" sample-efficiency curve.
- Rating: High

### [9] Building Production-Ready Probes For Gemini (Kramár et al., 2026)
- URL: https://arxiv.org/abs/2601.11516
- Verified: yes
- Summary: Google DeepMind report on deploying activation probes as misuse (cyber-offence) mitigations in user-facing Gemini. Main finding: probes trained on short inputs fail under production distribution shift, especially long context and multi-turn; new probe architectures plus training on diverse distributions were both required for broad generalization. Probes are paired with prompted classifiers in a cascade for best accuracy/compute; AlphaEvolve is used to automate probe-architecture search and adaptive red teaming.
- Relevance to our hypothesis: Real-deployment evidence for long-context/multi-turn brittleness, which is the exact regime of a customer-service agent. Our evaluation must include long, multi-turn transcripts, and we should test whether routing statistics are *less* sensitive to context length than raw activations.
- Rating: High

### [10] Constitutional Classifiers: Defending against Universal Jailbreaks across Thousands of Hours of Red Teaming (Sharma et al., 2025) and Constitutional Classifiers++ (Cunningham et al., 2026)
- URL: https://arxiv.org/abs/2501.18837 ; https://arxiv.org/abs/2601.04603
- Verified: yes (both)
- Summary: Text-based input/output classifiers trained on synthetic data generated from a natural-language "constitution". >3,000 h of red teaming found no universal jailbreak; costs were +0.38% absolute refusals and 23.7% inference overhead. CC++ (Jan 2026) moves to exchange (full-context) classifiers, a two-stage cascade of cheap then expensive classifiers, and ensembles *linear probe classifiers* with external classifiers, reaching a 40x cost reduction vs. the baseline exchange classifier and 0.05% production refusal rate over 1,700 h of red teaming.
- Relevance to our hypothesis: The strongest *text-based* baseline family, and (CC++) evidence that production systems now ensemble activation probes with text classifiers. Report our monitor's overhead and refusal/false-block rate in the same units (% overhead, % production traffic flagged).
- Rating: Medium

### [11] Obfuscated Activations Bypass LLM Latent-Space Defenses (Bailey et al., 2024; ICLR 2026)
- URL: https://arxiv.org/abs/2412.09565
- Verified: yes
- Summary: Shows that SAE-based, probe-based and latent-OOD defenses can all be bypassed by *obfuscated activations*: attacks that jointly optimize for harmful behaviour and inconspicuous latents. Against harmfulness probes, recall drops from 100% to 0% while retaining ~90% jailbreak rate; adversarial training of the monitor does not restore robustness. Obfuscation has a cost on harder tasks (e.g. SQL generation quality degrades).
- Relevance to our hypothesis: The main adaptive-adversary threat model for any latent monitor. We must evaluate an attacker who knows the routing monitor and optimizes inputs to keep routing "normal"; the discrete/top-k nature of routing may make gradient-based obfuscation harder or easier, which is an open question worth testing.
- Rating: High

### [12] RL-Obfuscation: Can Language Models Learn to Evade Latent-Space Monitors? (Gupta and Jenner, 2025)
- URL: https://arxiv.org/abs/2506.14261
- Verified: yes
- Summary: Fine-tunes 7B-14B models with RL against latent monitors. Token-level (mean/median-aggregated) monitors are highly vulnerable; max-pooling and attention-based probes remain robust. Evasion generalizes to unseen monitors and can be made conditional on specific inputs; models "repurpose tokens" to carry different internal representations.
- Relevance to our hypothesis: Directly informs aggregation design: sequence-level routing statistics should use max/attention pooling rather than mean expert-usage, and robustness to policy-side adaptation must be reported separately from input-side attacks.
- Rating: High

### [13] Linear probes rely on textual evidence: Results from leakage mitigation studies in language models (Boxo et al., 2025)
- URL: https://arxiv.org/abs/2509.21344
- Verified: yes
- Summary: Tests whether white-box probes detect *behaviour* or merely *surface text*. Removing textual evidence (system prompts, CoT that verbalize the behaviour) reduces AUROC by 10-30 points on Sandbagging, Sycophancy and Bias; with model organisms trained to suppress verbalization, AUROC drops to 0.57 vs 0.74 (Bias) and 0.57 vs 0.94 (Sandbagging).
- Relevance to our hypothesis: Critical confound for us: a routing shift on "user asks agent to write code" may just be a topic/lexical shift (code tokens route to code experts), not a "task-deviation" signal. We need leakage controls: benign code talk in-scope vs. off-task code writing, and injections with paraphrased/obfuscated surface forms.
- Rating: High

### [14] The Impact of Off-Policy Training Data on Probe Generalisation (Kirch et al., 2025; ACL 2026)
- URL: https://arxiv.org/abs/2511.17408
- Verified: yes
- Summary: Studies training probes on synthetic/off-policy responses across eight behaviours and several models, with linear and attention probes. Generalization failures are largest for behaviours defined by *response intent* (e.g. strategic deception) rather than surface features; successful generalization to "incentivised" data strongly predicts on-policy performance, giving a practical proxy test. Off-policy data sometimes yields more reliable probes than on-policy data from a different context.
- Relevance to our hypothesis: Our positives (agent actually deviating) will often be synthesized; use the incentivised-data proxy test and report on-policy vs. off-policy results separately.
- Rating: Medium

### [15] When AUC 0.998 Is Not Enough: A Candidate Evaluation Protocol for Hidden-State Probes of Indirect Prompt Injection in Multimodal Computer-Use Agents (Li et al., 2026; EvalMG workshop at SIGIR 2026)
- URL: https://arxiv.org/abs/2606.22864
- Verified: yes
- Summary: On Qwen2.5-VL-7B / Mind2Web (teacher-forced), a linear hidden-state probe reaches AUC 0.998 on clean-vs-attack, but the authors show this is not evidence of malicious-content detection: text-derived scalar baselines and visual control sets explain much of the separation. Proposes control sets and reporting heuristics; notes labels reflect injection-surface *presence*, not attack success.
- Relevance to our hypothesis: A must-follow evaluation caveat: include trivial baselines (length, token-type ratios, presence of instruction-like text), separate "injection present" from "agent actually deviated", and only claim detection of deviation when the probe beats the trivial features.
- Rating: High

### [16] What Features in Prompts Jailbreak LLMs? Investigating the Mechanisms Behind Attacks (Kirch et al., 2024, rev. 2025)
- URL: https://arxiv.org/abs/2411.03343
- Verified: yes
- Summary: Builds a dataset of 10,800 jailbreak attempts across 35 attack methods and trains linear and non-linear probes on prompt hidden states to predict *attack success* before generation. Probes are accurate on known attack families but transfer poorly across families, implying distinct mechanisms rather than one universal direction; causal latent interventions show success-linked features are encoded non-linearly.
- Relevance to our hypothesis: Establishes "predict attack success from pre-generation internals" as a task and metric, and warns that cross-attack-family generalization is the hard case; routing features should be evaluated with per-family hold-outs.
- Rating: Medium

### [17] Refusal in Language Models Is Mediated by a Single Direction (Arditi et al., 2024; NeurIPS 2024)
- URL: https://arxiv.org/abs/2406.11717
- Verified: yes
- Summary: Across 13 open chat models up to 72B, refusal is mediated by a one-dimensional residual-stream subspace found via difference-in-means over harmful vs. harmless instructions; ablating it removes refusal, adding it induces refusal. Also analyses how adversarial suffixes suppress propagation of this direction.
- Relevance to our hypothesis: Difference-in-means over contrast sets is the standard recipe; the "adversarial suffix suppresses the direction" analysis suggests a test of whether jailbreak/injection suppresses *safety-expert* routing in MoE layers.
- Rating: Medium

### [18] How Alignment and Jailbreak Work: Explain LLM Safety through Intermediate Hidden States (Zhou et al., 2024; Findings of EMNLP 2024)
- URL: https://arxiv.org/abs/2406.05644
- Verified: yes
- Summary: Uses weak (linear) classifiers on intermediate hidden states across 7B-70B models of several families: malicious vs. normal inputs are separable already in *early* layers (a pre-training artefact), alignment links this to "emotion" features in middle layers and to refusal tokens late; jailbreaks disrupt the early-to-middle transformation.
- Relevance to our hypothesis: Layer-wise story to replicate on routers: check whether injection/deviation is visible in early-layer routing (topic) vs. middle-layer routing (intent), and whether attacks perturb the middle layers specifically.
- Rating: Medium

### [19] Real-Time Detection of Hallucinated Entities in Long-Form Generation (Obeso et al., 2025)
- URL: https://arxiv.org/abs/2509.03531
- Verified: yes
- Summary: Trains token-level linear probes on hidden activations to flag hallucinated entities *during* streaming generation for 70B models. Across four model families the probes outperform semantic entropy (AUC 0.90 vs 0.71 for Llama-3.3-70B), generalize from entities to wrong answers in maths reasoning, and datasets transfer across models.
- Relevance to our hypothesis: Template for per-token, streaming, generation-time monitoring with token-level labels, which is what an "intercept mid-response" system needs; also a concrete example where a cheap internal probe beats an expensive sampling-based method.
- Rating: Medium

### [20] AI Control: Improving Safety Despite Intentional Subversion (Greenblatt et al., 2023; ICML 2024)
- URL: https://arxiv.org/abs/2312.06942
- Verified: yes
- Summary: Introduces control protocols with an untrusted strong model (GPT-4), a trusted weak model (GPT-3.5) and scarce trusted labour. "Trusted monitoring" asks the weak model to rate suspiciousness and audits the top ~2%; trusted editing and untrusted monitoring with anti-collusion improve substantially on baselines against an adversary that deliberately subverts the protocol.
- Relevance to our hypothesis: Provides the *framing* for a cheap monitor: safety vs. usefulness at a fixed audit budget, evaluated against a red team. A routing monitor is a candidate ultra-cheap trusted monitor and should be evaluated with safety/usefulness curves at a fixed audit rate. (Companion: Terekhov et al. 2025, below, show that text monitors themselves can be prompt-injected.)
- Rating: Medium

## Additional verified references (brief)
All fetched and verified; kept short to stay within the entry budget.
- Adaptive Attacks on Trusted Monitors Subvert AI Control Protocols (Terekhov et al., 2025) https://arxiv.org/abs/2510.09462 -- prompt injections placed in the untrusted model's outputs bypass diverse LLM text monitors; Defer-to-Resample amplifies the injection. Motivates monitors that read internals rather than text. Rating: Medium.
- Improving Alignment and Robustness with Circuit Breakers (Zou et al., 2024; NeurIPS 2024) https://arxiv.org/abs/2406.04313 -- representation-engineering intervention that reroutes harmful representations; extends to agents. Intervention rather than detection baseline. Rating: Medium.
- GradSafe: Detecting Jailbreak Prompts for LLMs via Safety-Critical Gradient Analysis (Xie et al., 2024; ACL 2024) https://arxiv.org/abs/2402.13494 -- gradient-pattern detector on safety-critical parameters; training-free; beats Llama Guard on ToxicChat/XSTest but needs a backward pass. Rating: Low.
- Semantic Entropy Probes (Kossen et al., 2024) https://arxiv.org/abs/2406.15927 -- linear probes on hidden states approximate semantic entropy from one forward pass (vs. 5-10x sampling cost); layer/position ablations. Rating: Low.
- Beyond Linear Probes: Dynamic Safety Monitoring for Language Models (Oldfield et al., 2025; ICLR 2026) https://arxiv.org/abs/2509.26238 -- truncated polynomial classifiers as an anytime "safety dial"/cascade; match or beat MLP probes on WildGuardMix/BeaverTails for 4 models up to 30B. Rating: Medium.
- Speculative Probing: LLM Monitoring at Speculative-Decoding Cost (Zhang et al., 2026) https://arxiv.org/abs/2608.28099 -- reuses the speculative-decoding head plus soft prompts as a near-zero-overhead classifier; beats zero-shot GPT-5.4-mini and matches Llama-Guard-3-8B on Qwen 3.5 4B/9B/27B. Rating: Medium (cost baseline).
- Calibrate-Then-Delegate (Pona et al., 2026) https://arxiv.org/abs/2604.14251 -- probe-to-expert cascade with a "delegation value" probe and finite-sample risk/budget guarantees on four safety datasets. Rating: Low-Medium.
- Mechanistic Anomaly Detection for "Quirky" Language Models (Johnston et al., 2025; ICLR Building Trust workshop) https://arxiv.org/abs/2504.08812 -- unsupervised detectors on internal features flag distribution shift; no detector works across all models/tasks. Rating: Medium (unsupervised framing).
- Red-teaming Activation Probes using Prompted LLMs (Blandfort and Graham, 2025) https://arxiv.org/abs/2511.00554 -- black-box LLM red-teaming loop reveals interpretable probe failure modes (legalese false positives, bland-procedural-tone false negatives). Rating: Medium.
- Pressure-Testing Deception Probes in LLMs (Kumar, 2026; GEM workshop at ACL 2026) https://arxiv.org/abs/2605.27958 -- Gemma 3 1B-27B: AUROC >=0.998 on clean data collapses under stylistic shift; style augmentation recovers 0.979-0.983; single direction captures only 0.61-0.80. Rating: Medium.
- Activation Probes Surface Code-Security Signals that the Model's Output Misses (Wiryadi, 2026; TAIGR workshop at ICML 2026) https://arxiv.org/abs/2608.09643 -- probe ranks vulnerable above fixed function in 61-67% of pairs across five models where the model's verbal verdict is unchanged. Rating: Medium (internal beats output).
- Probing the Misaligned Thinking Process of Language Models (Zhou et al., 2026) https://arxiv.org/abs/2606.24251 -- 18 "misalignment indicator" linear probes; 0.935 AUROC OOD across 5 behaviours at low benign FPR, matching a strong LLM judge. Rating: Medium.
- From Reward-Hack Activations to Agentic Risk States (Wilhelm and Kao, 2026) https://arxiv.org/abs/2606.06223 -- ReAct agents in Gameable ALFWorld/WebShop; activation score alone is a latent state, not an action predictor; combining with entropy and decision context gives better risk estimates. Rating: Medium (agent-context calibration).
- When Agents Go Rogue: Activation-Based Detection of Malicious Behaviors in Multi-Agent Systems (Xu et al., 2026; ICML 2026) https://arxiv.org/abs/2607.06807 -- AcMAS uses internal states to detect compromised agents; F1 0.94 vs 0.72 (sync) and 0.93 vs 0.38 (async) over graph baselines. Rating: Medium.
- Hiding in Plain Text: Detecting Concealed Jailbreaks via Activation Disentanglement (Farzam et al., 2026) https://arxiv.org/abs/2602.19396 -- disentangles goal vs. framing factors in activations (ReDAct) and builds an anomaly detector (FrameShield). Rating: Medium.
- Defending LLMs Against Jailbreak Attacks via In-Decoding Safety-Awareness Probing (Zhao et al., 2026) https://arxiv.org/abs/2601.10543 -- latent safety signals persist during generation even when jailbroken; used for early in-decoding detection. Rating: Medium.
- Monitoring Decomposition Attacks in LLMs with Lightweight Sequential Monitors (Yueh-Han et al., 2025) https://arxiv.org/abs/2506.10949 -- cumulative lightweight text monitor over subtasks: 93% defence, 90% lower cost, 50% lower latency than reasoning monitors. Rating: Low-Medium (cheap text baseline).
- Activation Oracles (Karvonen et al., 2025) https://arxiv.org/abs/2512.15674 -- LLMs trained to explain activations in natural language; recover fine-tuned malign propensities absent from text. Rating: Low.

## MoE-adjacent references found while scoping the gap (verified)
- RouteScan: A Non-Intrusive Approach to Auditing MoE LLMs Safety via Expert Routing Telemetry (Lv et al., 2026) https://arxiv.org/abs/2605.24817 -- uses GPU-thread allocation to experts during *prefill* as a side-channel proxy for routing and classifies harmful vs. benign prompts; AUROC >0.91 on unseen harmful domains across four MoE LLMs. Prompt-level harmfulness only; no task-drift, no generation-time trajectory, no agent setting.
- RASET: Router-Agnostic Safety-Critical Expert Tuning Exposes Localized Safety Enforcement Failures in MoE LLMs (Zhang et al., 2026; EMNLP 2026) https://arxiv.org/abs/2605.29708 -- finds routing in aligned MoE LLMs is largely *topic-driven* and that safety behaviour can be flipped with little change to the routing path. This is a direct challenge to our hypothesis: routing may track topic, not intent/deviation.

## Key takeaways for our project
- Frame the problem as *task drift* (Abdelnabi et al.) and reuse TaskTracker + BIPIA + Open-Prompt-Injection/deepset as datasets, with attack-family hold-outs (train on none of the attacks, test on injections/jailbreaks/malicious instructions).
- Use the contrast/delta design: routing statistics of the same conversation prefix before vs. after untrusted content (activation-delta analogue), and difference-in-means over contrast pairs (sleeper-agent and refusal-direction recipes). Report which layers carry the signal; dense work consistently finds middle layers most informative.
- Baselines to include, at matched compute: (i) linear probe on residual stream at the same layer (PIShield / TaskTracker), (ii) Attention Tracker (training-free attention signal, also zero extra inference), (iii) a prompted LLM judge and a fine-tuned small guard model (Llama-Guard-3-8B style), (iv) trivial text scalars (length, instruction-like-token ratio) as required by Li et al. 2026, (v) probe-plus-LLM cascade (McKenzie et al., CC++).
- Metrics: AUROC plus recall at fixed 1% FPR on benign traffic (Goldowsky-Dill), FPR/FNR (PIShield), attack success rate after interception (InstructDetector), % inference overhead and % production traffic flagged (Constitutional Classifiers), safety/usefulness at a fixed audit budget (AI Control), and detection *latency* in tokens for streaming interception (Obeso et al.).
- Aggregation matters for robustness: mean-pooled token-level monitors are evaded by RL-Obfuscation; max-pooling or attention-weighted aggregation of per-token routing vectors is the safer default.
- Known failure modes to test for explicitly: (a) surface/lexical leakage (Boxo et al.; RASET says routing is topic-driven) -- include in-scope code discussion vs. off-task code execution controls; (b) long-context and multi-turn shift (Kramár et al.; Kulkarni); (c) stylistic shift (Kumar 2026); (d) off-policy vs. on-policy positives (Kirch et al. 2025); (e) adaptive attackers who optimize inputs to keep latents/routing "normal" (Bailey et al.) and policy-side RL evasion (Gupta and Jenner).
- Separate "injection present" from "agent deviated": label whether the model actually followed the injected task, not only whether the injection was in context (Li et al. 2026; Kirch et al. 2024 attack-success prediction).
- Treat internal monitors as a first-stage filter in a hierarchy; the compelling result to aim for is equal accuracy to an LLM judge at orders-of-magnitude less cost, or earlier detection (before/while generating) than any output monitor.
- Where internal monitors demonstrably beat text monitors: outputs that look benign while intent is off (deception probes), signals the model's own verdict misses (code-security probes), semantic-entropy-level hallucination detection from one pass, and text monitors that are themselves prompt-injectable (Terekhov et al.).

## Closest prior work
1. Abdelnabi et al. 2024, "Get my drift?" (TaskTracker): identical problem definition (task drift under injected instructions) and identical strategy (a cheap linear detector on internal state deltas), just on dense activations rather than routing. Our contribution must be framed as "routing as a sparser, cheaper, already-computed projection of the same signal", evaluated on their data.
2. Hung et al. 2025, Attention Tracker: the same "reuse a by-product of the forward pass at zero extra inference" argument, with head selection analogous to selecting sensitive router layers/experts.
3. Kulkarni 2026, Latent Adversarial Detection: internal-state *trajectory* features over turns for agentic multi-turn attacks; the closest thing to monitoring a routing trace over a conversation.

## Open gap
- No verified work uses the MoE *router decisions* (top-k expert indices / gate probabilities per token per layer) as a runtime task-drift or prompt-injection monitor for an agent. RouteScan (2026) is the nearest, but it classifies prompt-level harmfulness from GPU telemetry during prefill; it has no notion of task deviation, no generation-time or per-turn trajectory, and no agentic scenario.
- Nobody has compared a routing-based monitor against the established dense baselines (TaskTracker-style activation probes, Attention Tracker, LLM judges) at matched compute, nor measured detection latency in tokens for mid-generation interception.
- The robustness literature (obfuscated activations, RL-Obfuscation) has only attacked continuous latents; whether discrete top-k routing is harder or easier to obfuscate is untested.
- RASET's finding that routing is largely topic-driven means the central scientific question is open: does routing carry an intent/deviation signal beyond topic? A leakage-controlled study (Boxo-style) that holds topic fixed while varying task compliance would settle this and is, as far as this search shows, not yet done.
