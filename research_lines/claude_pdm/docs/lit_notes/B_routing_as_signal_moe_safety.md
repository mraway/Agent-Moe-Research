# Theme B: Routing as a signal; MoE-specific safety

Search date: 2026-09-02. All "Verified: yes" entries were confirmed by fetching the arXiv abstract (and, where noted, the HTML full text). Entries are ordered by relevance: direct prior work first, then routing-as-feature work, MoE safety-expert / steering work, routing attacks, side-channel / fingerprinting, specialization caveats, and adjacent dense-model work.

## Entries

### [1] RouteScan: A Non-Intrusive Approach to Auditing MoE LLMs Safety via Expert Routing Telemetry (Bo Lv et al., 2026, arXiv 2605.24817, v2 Aug 2026)
- URL: https://arxiv.org/abs/2605.24817
- Verified: yes (abstract + HTML full text)
- Summary: **DIRECT PRIOR WORK.** Detects harmful prompts from *expert-routing-induced workload* rather than text. Signal: number of active GPU threads per expert module during prefill (via Nsight), shown to be linear in routed token load, so it is effectively a per-layer expert-load histogram. Features = normalized per-layer expert-load distribution (fine-grained block) + 4 layer-level statistics (activation coverage rate, effective activation rate, coverage gap, coverage concentration); classifier = logistic regression with adaptive regularization + Platt calibration. Models: DeepSeek-V2-Lite-Chat, Qwen1.5-MoE-A2.7B-Chat, GPT-OSS-20B, OLMoE-1B-7B-0125-Instruct. Data: AdvBench, StrongREJECT, HEx-PHI, CatHarmfulQA, HarmBench vs. benign prompts built by swapping malicious keywords for safe ones. Results: cross-domain AUROC > 0.91 on all models (OLMoE: AdvBench 0.9985, StrongReject 0.9841, CatHarmfulQA 0.9336, HarmBench 0.9911); transfers to unseen jailbreak wrappers (AutoDAN -> PAIR, CodeChameleon). Prefill-only, per-prompt; no decode-phase or per-token monitoring; no agent / task-deviation setting.
- Relevance to our hypothesis: Establishes that aggregated expert-load histograms linearly separate harmful from benign prompts on the exact open MoE models we would use. Our work differs in (a) using router logits/top-k indices directly, (b) monitoring the decode-phase routing trajectory token-by-token, (c) targeting *task deviation* (e.g., customer-service agent coaxed into coding / injected instructions), not harmfulness, and (d) multi-turn agent context.
- Rating: High

### [2] Mixture-of-Expert Blocks Contain Strong Hallucination Detection Signals (Joao Fonseca et al., 2026, arXiv 2608.17687)
- URL: https://arxiv.org/abs/2608.17687
- Verified: yes (abstract + HTML full text)
- Summary: **DIRECT PRIOR WORK (routing as per-token runtime signal).** InnerExpert extracts six MoE-specific per-token signals during generation: router entropy, expert hidden score, expert similarity (routing-weighted cosine among activated experts' outputs), cumulative expert-usage distribution over the generated prefix, Gini impurity of usage, inverse Herfindahl "effective number of experts"; plus standard hidden/attention scores. Fed to a light classifier (LogReg/RF/XGBoost/MLP/Transformer; XGB/MLP best), labels via LLM-as-judge. Models: OLMoE-1B-7B-0924-Instruct (64 experts, top-8) and Gemma-4-26B-A4B-it (128 routed + 1 shared, top-8). Datasets: RealtimeQA, SQuAD, TruthfulQA, NQ-Open, FreshQA. Answer-level AUROC 0.882 (OLMoE) / 0.912 (Gemma) vs HaluNet 0.851/0.901, SelfCheckGPT 0.784/0.707; token-level AUROC 0.762/0.753 vs best baseline ~0.64. On Gemma, router entropy alone gives 0.884 answer-level AUROC.
- Relevance to our hypothesis: Closest methodological template: cheap routing statistics computed cumulatively over the decoded sequence + a small classifier, single forward pass. Their target is hallucination, not task deviation; they do not analyze routing trajectories qualitatively nor multi-turn/agent settings.
- Rating: High

### [3] MASCing: Configurable Mixture-of-Experts Behavior via Activation Steering Masks (Jona te Lintelo et al., 2026, arXiv 2604.27818)
- URL: https://arxiv.org/abs/2604.27818
- Verified: yes (abstract + HTML full text)
- Summary: Trains an LSTM surrogate that takes the *sequence of unnormalized routing logits from all MoE layers* over tokens and predicts a binary behavior label (e.g., refusal vs. compliance); the surrogate is then used as a differentiable proxy to optimize expert steering masks. Seven MoE models (DeepSeek-MoE-16B-Chat, GPT-OSS-20B, Hunyuan-A13B, Mixtral-8x7B-Instruct, Phi-3.5-MoE, Qwen1.5-MoE-A2.7B, Qwen3-30B-A3B). Multi-turn jailbreak defense success rises from 52.5% to 83.9% average (up to 89.2%); adult-content relaxation 52.6% -> 82.0%. Standalone classification accuracy of the surrogate is not reported.
- Relevance to our hypothesis: Demonstrates that a sequence model over per-layer routing logits carries enough information to predict downstream behavior, and does so in a multi-turn adversarial setting. It is used for steering rather than as a runtime detector, and does not study task deviation.
- Rating: High

### [4] Steering MoE LLMs via Expert (De)Activation (Mohsen Fayyaz et al., ICLR 2026, arXiv 2509.09660)
- URL: https://arxiv.org/abs/2509.09660 (code: https://github.com/adobe-research/SteerMoE)
- Verified: yes (abstract + HTML full text)
- Summary: Detects behavior-linked experts via Risk Difference of activation rate between paired prompt sets with opposite behaviors (delta_i = p_i^(1) - p_i^(2)); steers by setting router logits of chosen experts to max+eps / min-eps. Models: GPT-OSS-120B/20B, Qwen3-30B-A3B, OLMoE-1B-7B, Phi-3.5-MoE, Mixtral-8x7B. Safety +15-20% (up to +20%), faithfulness up to +27%; adversarial steering -41% safety alone, -100% combined with AIM jailbreak. Fig. A.6 shows safe tokens route to "safe" experts and unsafe tokens to "unsafe" experts, but no detection accuracy is reported and only single-turn prompts are evaluated.
- Relevance to our hypothesis: Provides the contrastive expert-frequency methodology we can reuse to build a "task profile" (normal-task experts vs. deviation experts), and confirms token-level discriminability of routing. Also a warning: routing can be manipulated at inference, so a routing monitor must be robust to router-logit tampering.
- Rating: High

### [5] Understanding Safety-Sensitive Expert Behavior in Mixture-of-Experts LLMs (RASET) (Zhibo Zhang et al., EMNLP 2026 Main, arXiv 2605.29708)
- URL: https://arxiv.org/abs/2605.29708
- Verified: yes (abstract + HTML full text)
- Summary: Observational routing analysis on OLMoE-1B-7B, DeepSeek-V2-Lite-Chat, Qwen3-30B-A3B-Instruct-2507, Phi-3.5-MoE, GPT-OSS-20B. Key numbers (JS divergence of routing-weight vectors / top-8 overlap): refusal vs compliant teacher-forced continuations differ very little (JS 0.0054-0.0434, top-8 overlap 7.18-7.92 of 8); adding a refusal prefix with topic fixed gives JS 0.0098-0.0346 vs 0.3379 when the topic changes; matched harmful/benign pairs JS 0.1006 vs 0.2282-0.2362 for random cross-topic pairs. Conclusion: routing is largely *topic-driven*; safety behavior can be altered with little change to routing; RASET tunes only safety-critical experts and reaches 50.5% high-quality ASR (+37.6 pts) on five backbones. Authors explicitly argue routing alone is an unreliable *safety* signal.
- Relevance to our hypothesis: The most important piece of evidence, cutting both ways. Topic-driven routing strongly supports detecting *task/domain deviation* (support chat -> code, injected new instructions) via routing; but it warns that a change of *intent* with the topic held fixed produces only a small routing shift, so pure refusal/harm detection from routing is weak.
- Rating: High

### [6] Safety-Oriented Routing Analysis of Mixtral MoE Under Benign and Harmful Prompts (Md Nurul Absar Siddiky, 2026, arXiv 2605.24270)
- URL: https://arxiv.org/abs/2605.24270
- Verified: yes (abstract + HTML full text)
- Summary: Mixtral-8x7B-Instruct only; 10 benign + 10 harmful prompts for routing statistics (expert frequency, per-layer entropy, effective expert count, k80/k90/k95 coverage), plus 100 harmful prompts for suppression experiments. Activation-based expert usage is broad/long-tailed with only modest benign-vs-harmful separation (k80: 176 vs 174 experts); gradient-based router sensitivity is far more concentrated (k80: 94 vs 92) and peaks in final layers; activation selectivity peaks at layers 8-15. Suppressing top-5 benign-dominant experts changed restricted responses 24 -> 14 per 100. No classifier trained, no AUROC.
- Relevance to our hypothesis: Small-scale confirmation that raw expert-frequency histograms differ only modestly between benign and harmful prompts on Mixtral, and that mid-layer routing is the most selective. Supports using mid-layer routing and richer trajectory features rather than global histograms.
- Rating: Medium

### [7] Expert Selections In MoE Models Reveal (Almost) As Much As Text (Amir Nuriyev & Gabriel Kulp, 2026, arXiv 2602.04105)
- URL: https://arxiv.org/abs/2602.04105
- Verified: yes
- Summary: Reconstructs input tokens purely from expert-selection (routing) sequences. On 32-token OpenWebText sequences (100M training tokens), a 3-layer MLP reaches 63.1% top-1 token recovery and a transformer sequence decoder 91.2% top-1 / 94.8% top-10, versus a weak logistic-regression baseline in prior work. Noise on routing mitigates but does not remove leakage; authors argue routing should be treated as sensitive as text.
- Relevance to our hypothesis: Strong evidence that top-k expert indices per layer per token retain nearly the full information content of the text, so a routing-based task-deviation detector is information-theoretically well-founded (and cheap, since routing is already computed).
- Rating: High

### [8] Your Mixture-of-Experts LLM Is Secretly an Embedding Model For Free (Ziyue Li & Tianyi Zhou, 2024, arXiv 2410.10814)
- URL: https://arxiv.org/abs/2410.10814
- Verified: yes
- Summary: MoEE uses the concatenated routing weights (RW) of all MoE layers as a training-free sentence embedding, combined with hidden states via a weighted similarity sum. Across 6 MTEB task types / 20 datasets, RW embeddings are more robust to prompt wording than hidden states and capture "high-level semantics"; MoEE gives significant gains over hidden-state-only embeddings with no fine-tuning.
- Relevance to our hypothesis: Directly justifies treating routing-weight vectors as a semantic representation of what the model is "doing"; a running task-embedding built from routing weights is the natural feature for detecting drift from the intended task.
- Rating: High

### [9] Does the Same Token Mean the Same State? MoE Routing as Signal for Reasoning Control (Kang Chen et al., 2026, arXiv 2606.22798)
- URL: https://arxiv.org/abs/2606.22798
- Verified: yes
- Summary: Shows that for the same token id, the routing state still separates *task context, trajectory history, and reasoning-effort mode*. Uses routing states at "boundary" and "delimiter" anchor tokens (e.g., \boxed{}, code fences) with a weighted-Jaccard k-NN over routing states for answer-string-free Routing Agreement Decoding on gpt-oss and Qwen3-MoE (10 sparse-MoE configs, 6 datasets): RAD 73.9% / RAD+DC 74.2% vs majority voting 73.6%.
- Relevance to our hypothesis: Independent evidence that the routing trajectory encodes task context and history, not just the current token, and that routing-state similarity (weighted Jaccard over top-k sets) is a usable distance for comparing trajectories, which is exactly what a "deviation from the normal routing profile" detector needs.
- Rating: High

### [10] RouteHijack: Routing-Aware Attack on Mixture-of-Experts LLMs (Zhiyuan Xu et al., 2026, arXiv 2605.02946)
- URL: https://arxiv.org/abs/2605.02946
- Verified: yes (abstract + HTML full text)
- Summary: Localizes safety-critical vs harmful experts by contrasting activations under refusals vs harmful completions, then optimizes an input-only adversarial suffix with a ternary routing loss. 69.3% average ASR across seven MoE LLMs (Qwen3-30B-A3B, Phi-3.5-MoE, Mixtral-8x7B, Qwen1.5-MoE, DeepSeek-MoE-16B, Hunyuan-A13B, Pangu-Pro-MoE-72B), 3.2x prior optimization attacks; zero-shot transfer raises ASR 27.7% -> 61.2% on five siblings and 2.47% -> 38.7% on three MoE VLMs. Discussion section: the attack suppresses safety-expert probability by >25% at the boundary token, and the authors propose "lightweight anomaly detection classifiers on the pre-softmax router logits" as an internal defense that could intercept context-injection attacks before generation.
- Relevance to our hypothesis: An attack paper that explicitly names our defense idea (router-logit anomaly detection) as future work, without implementing it. Also gives measured routing shifts (Target Expert Suppression -28.75%, Harmful Promotion +28.51%) that a detector could key on.
- Rating: High

### [11] SAFEx: Analyzing Vulnerabilities of MoE-Based LLMs via Stable Safety-critical Expert Identification (Zhenglin Lai et al., NeurIPS 2025, arXiv 2506.17368)
- URL: https://arxiv.org/abs/2506.17368 (OpenReview: https://openreview.net/forum?id=VwsXmcMyg5)
- Verified: yes
- Summary: Stability-based selection identifies safety-critical experts in Qwen3-30B-A3B (6,144 experts / 48 layers), split into a Harmful Content Detection Group and a Harmful Response Control Group. Disabling 12 experts cuts refusal rate by 22%; LoRA with negative weight merging on those experts restores refusal under adversarial prompts.
- Relevance to our hypothesis: Shows that a small set of experts is preferentially routed to for harmful content ("detection group"), so the activation frequency of those experts is itself a runtime signal. Single-turn, no detector trained.
- Rating: Medium

### [12] GateBreaker: Gate-Guided Attacks on Mixture-of-Expert LLMs (Lichao Wu et al., 2025, arXiv 2512.21008)
- URL: https://arxiv.org/abs/2512.21008
- Verified: yes
- Summary: Training-free attack: gate-level profiling finds experts that disproportionately activate on harmful prompts, expert-level localization finds safety neurons, then disables ~3% of neurons. ASR 7.4% -> 64.9% on eight aligned MoE LLMs; transfer 67.7% vs 17.9% baseline; 60.9% on five MoE VLMs.
- Relevance to our hypothesis: "Gate-level profiling" is precisely routing-as-signal used offensively; the harmful-vs-benign routing statistics it relies on are the same ones a monitor would use.
- Rating: Medium

### [13] Large Language Lobotomy: Jailbreaking Mixture-of-Experts via Expert Silencing (Jona te Lintelo et al., 2026, arXiv 2602.08741)
- URL: https://arxiv.org/abs/2602.08741
- Verified: yes
- Summary: L3 learns routing patterns correlated with refusal, attributes safety to specific experts, and adaptively silences them: ASR 7.3% -> 70.4% average (max 86.3%) on eight open MoE LLMs while silencing <20% of layer-wise experts.
- Relevance to our hypothesis: Confirms refusal-correlated routing patterns exist and are learnable across many models; also a threat model (expert silencing changes the routing trajectory itself).
- Rating: Medium

### [14] RASA: Routing-Aware Safety Alignment for Mixture-of-Experts Models (Jiacheng Liang et al., 2026, arXiv 2602.04448)
- URL: https://arxiv.org/abs/2602.04448
- Verified: yes
- Summary: Computes an Adversarial Activation Discrepancy to find experts disproportionately activated during successful jailbreaks (Qwen3-30B-A3B, OLMoE-1B-7B), fine-tunes only those with routing fixed, and enforces routing consistency with safety-aligned contexts; near-perfect jailbreak robustness with reduced over-refusal and preserved MMLU/GSM8K/TruthfulQA.
- Relevance to our hypothesis: Shows successful jailbreaks have a measurable expert-activation discrepancy relative to benign prompts, which is a detectable routing signature; also shows "routing enforcement" as an intervention lever.
- Rating: Medium

### [15] Sparse Models, Sparse Safety: Unsafe Routes in Mixture-of-Experts LLMs (Yukun Jiang et al., 2026, arXiv 2602.08621)
- URL: https://arxiv.org/abs/2602.08621
- Verified: yes
- Summary: Defines Router Safety Importance Score (RoSais); masking 5 high-RoSais routers in DeepSeek-V2-Lite raises ASR >4x to 0.79 on JailbreakBench; F-SOUR token-layer-wise stochastic route optimization reaches ASR 0.90 (JailbreakBench) / 0.98 (AdvBench) across four MoE families. Defenses proposed: safety-aware route disabling and router training (no detection).
- Relevance to our hypothesis: "Unsafe routes" are concrete routing configurations that produce harmful output; a detector could flag entry into such routes. No monitoring is implemented.
- Rating: Medium

### [16] Misrouter: Exploiting Routing Mechanisms for Input-Only Attacks on Mixture-of-Experts LLMs (Zekun Fei et al., 2026, arXiv 2605.04446)
- URL: https://arxiv.org/abs/2605.04446
- Verified: yes (abstract; numbers not in abstract)
- Summary: Identifies weakly aligned experts from expert-activation patterns on harmful prompts / harmful compliance, then optimizes inputs to route toward them and away from strongly aligned experts, with a second phase that preserves routing stability.
- Relevance to our hypothesis: Another input-only routing-manipulation attack; relevant as an adversary that deliberately shapes the routing trajectory, which a routing monitor must be evaluated against.
- Rating: Medium

### [17] Mixture of Tunable Experts: Behavior Modification of DeepSeek-R1 at Inference Time (Robert Dahlke et al., 2025, arXiv 2502.11096)
- URL: https://arxiv.org/abs/2502.11096
- Verified: yes
- Summary: "functional Token Resonance Imaging" (prompts eliciting a behavior, then expert-activation statistics) finds refusal-relevant experts in DeepSeek-R1; switching off the top-10 (0.07% of 14,848 routed experts) cuts refusals by 52% on sensitive prompts with MT-Bench preserved; forcing them on increases refusals.
- Relevance to our hypothesis: Early demonstration that behavior-specific experts can be found from routing statistics and toggled at inference on a frontier-scale MoE; a possible interception mechanism (force "on-task" experts).
- Rating: Medium

### [18] Expert-Aware Refusal Steering (Anna C. Marbut et al., 2026, under review COLM 2026, arXiv 2606.04160)
- URL: https://arxiv.org/abs/2606.04160
- Verified: yes
- Summary: Extends refusal steering vectors to three open MoE LLMs using refusal-specific expert routing patterns and expert-specific steering directions; refusal can be steered from a single expert's output, but "refusal signals captured by steering methods differ from expert routing behavior", implicating attention as well.
- Relevance to our hypothesis: Caution that routing and residual-stream refusal directions are not the same signal; combining routing with a hidden-state probe may be needed.
- Rating: Medium

### [19] MoEcho: Exploiting Side-Channel Attacks to Compromise User Privacy in Mixture-of-Experts LLMs (Ruyi Ding et al., CCS 2025, arXiv 2508.15036)
- URL: https://arxiv.org/abs/2508.15036 (ACM: https://dl.acm.org/doi/10.1145/3719027.3765174)
- Verified: yes (abstract + HTML full text)
- Summary: Uses expert-load traces (token-to-expert distribution during prefill) and per-token expert sequences during decode, obtained via hardware side channels, to train classifiers that infer prompt attributes. Illness (116 classes) inferred at 99.9% (templated) / 56.5% (unstructured) on DeepSeek-V2-Lite and 100% / 74.4% on Qwen1.5-MoE; gender 81-100%; TinyMixtral (4 experts) much weaker (47.3% / 9.5%). Also response reconstruction and VLM variants.
- Relevance to our hypothesis: A clean demonstration that coarse expert-load histograms classify prompt *content/topic* with high accuracy on models with many experts, and that fine-grained models (60-64 experts) leak far more than Mixtral-style 8-expert models. Same machinery, different goal.
- Rating: High

### [20] Stealing User Prompts from Mixture of Experts (Itay Yona et al., 2024, arXiv 2410.22884)
- URL: https://arxiv.org/abs/2410.22884
- Verified: yes
- Summary: Exploits Expert-Choice routing and torch.topk tie handling so a co-batched attacker fully recovers a victim's prompt on a two-layer Mixtral (O(VM^2) queries, ~100 queries/token).
- Relevance to our hypothesis: Background on routing as an information channel; low direct relevance.
- Rating: Low

### [21] Buffer Overflow in Mixture of Experts (Jamie Hayes et al., 2024, arXiv 2402.05526)
- URL: https://arxiv.org/abs/2402.05526
- Verified: yes
- Summary: Routing strategies with cross-batch dependencies (expert capacity buffers) let malicious co-batched queries change outputs of benign queries; proof-of-concept in a toy setting; mitigations: batch-order randomization, larger buffers.
- Relevance to our hypothesis: Shows routing can be perturbed by other users' traffic, a nuisance source for a routing monitor in batched serving.
- Rating: Low

### [22] BadMoE: Backdooring Mixture-of-Experts LLMs via Optimizing Routing Triggers and Infecting Dormant Experts (Qingyue Wang et al., 2025, arXiv 2504.18598)
- URL: https://arxiv.org/abs/2504.18598
- Verified: yes
- Summary: First MoE-specific backdoor: poison under-utilized "dormant" experts, optimize a routing-aware trigger that routes to them, promote them to dominant via poisoned data; malicious target-task behavior with preserved utility. No routing-based defense discussed.
- Relevance to our hypothesis: A trigger that activates normally-dormant experts is an extreme routing anomaly; a routing monitor calibrated on normal expert usage should in principle detect it.
- Rating: Medium

### [23] Who Speaks for the Trigger? Dynamic Expert Routing in Backdoored Mixture-of-Experts Transformers (Xin Zhao et al., 2025, arXiv 2510.13462)
- URL: https://arxiv.org/abs/2510.13462
- Verified: yes
- Summary: BadSwitch couples trigger optimization with sensitivity-guided Top-S expert tracing and constrains Top-K gating to target experts; up to 100% ASR (94.07% ASR / 87.18% ACC on AGNews after defenses) on Switch Transformer, QwenMoE, DeepSeekMoE. No routing-based detection.
- Relevance to our hypothesis: Same as [22]; embeds the backdoor in the routing path itself.
- Rating: Low

### [24] RouteMark: A Fingerprint for Intellectual Property Attribution in Routing-based Model Merging (Xin He et al., 2025, arXiv 2508.01784)
- URL: https://arxiv.org/abs/2508.01784
- Verified: yes
- Summary: Routing Score Fingerprint (activation intensity per expert/task/layer) and Routing Preference Fingerprint (which inputs preferentially activate each expert) under probing inputs identify reused experts in merged CLIP-MoE models; robust to expert replacement/addition/deletion, fine-tuning, pruning, permutation; beats weight- and activation-based baselines.
- Relevance to our hypothesis: Establishes that per-expert routing statistics are stable, reproducible, task-discriminative signatures, the property a "normal task routing profile" relies on.
- Rating: Medium

### [25] PathMark: Protecting Intellectual Property of Mixture-of-Expert LLMs via Path Watermarks (Yudong Gao et al., CCS 2026, arXiv 2607.03688)
- URL: https://arxiv.org/abs/2607.03688
- Verified: yes
- Summary: Turns routing into a covert watermark channel by training tokens to traverse predetermined expert subsets on trigger; >99% verification, <2% perplexity cost on four MoE models; robust to quantization/fine-tuning/pruning.
- Relevance to our hypothesis: Shows routing paths can be deliberately shaped and read back with white-box routing inspection; tangential.
- Rating: Low

### [26] Bayesian Mixture-of-Experts: Towards Making LLMs Know What They Don't Know (Albus Yizhuo Li, 2025, arXiv 2509.23830)
- URL: https://arxiv.org/abs/2509.23830
- Verified: yes
- Summary: Puts a distribution over the routing decision (weight-space, logit-space, selection-space variants) in a 3B MoE; improves routing stability, calibration and OOD detection AUROC (numbers not in abstract).
- Relevance to our hypothesis: Routing uncertainty as an OOD signal, but requires modifying the router; our approach must work on frozen released routers.
- Rating: Medium

### [27] RepetitionCurse: Measuring and Understanding Router Imbalance in Mixture-of-Experts LLMs under DoS Stress (Ruixuan Huang et al., 2026, arXiv 2512.23995)
- URL: https://arxiv.org/abs/2512.23995
- Verified: yes
- Summary: Out-of-distribution (repetitive) prompts collapse routing so all tokens go to the same top-k experts, raising Mixtral-8x7B end-to-end latency 3.063x; presented as a DoS vulnerability, not a detector.
- Relevance to our hypothesis: Evidence that OOD inputs produce gross, easily measurable routing anomalies (load concentration), useful as a sanity baseline for anomaly features.
- Rating: Medium

### [28] The Myth of Expert Specialization in MoEs: Why Routing Reflects Geometry, Not Necessarily Domain Expertise (Xi Wang et al., 2026, arXiv 2604.09780)
- URL: https://arxiv.org/abs/2604.09780
- Verified: yes
- Summary: Across five pre-trained MoEs, routing follows hidden-state geometry (routers are linear maps); expert overlap is ~60% whether two runs answer the same or entirely different questions; prompt-level routing does not predict rollout-level routing; deeper layers show near-identical activation across unrelated inputs, especially in reasoning models.
- Relevance to our hypothesis: Major caveat: naive per-prompt expert-set overlap may not separate tasks; need layer selection (mid layers), distributional/trajectory features, and evaluation on rollouts, not just prompts.
- Rating: High

### [29] How Modular Is a Frontier Mixture-of-Experts? A Pre-registered Causal Test in Which Apparent Expert Modularity Mostly Dissolves (Tony Salomone et al., 2026, arXiv 2606.25092)
- URL: https://arxiv.org/abs/2606.25092
- Verified: yes
- Summary: Routing-mass atlas on Command A+ (218B, 128 experts, 8 active) with pre-registered ablations vs size-matched random experts: only 1 of 6 expert families (Arabic) passes a selectivity bar; modularity is corpus/metric/threshold dependent. Qwen3-30B-A3B as positive control.
- Relevance to our hypothesis: Routing mass can correlate with a domain without the experts being causally modular; for *detection* correlation suffices, but this warns against over-interpreting "task experts".
- Rating: Medium

### [30] The Illusion of Specialization: Unveiling the Domain-Invariant "Standing Committee" in Mixture-of-Experts Models (Yan Wang et al., ACL 2026 Main, arXiv 2601.03425)
- URL: https://arxiv.org/abs/2601.03425
- Verified: yes
- Summary: CommitteeAudit shows a compact expert coalition captures most routing mass across MMLU domains, layers and budgets on three models; peripheral experts carry domain-specific knowledge while the committee handles syntax/reasoning structure.
- Relevance to our hypothesis: Suggests the task-deviation signal lives in the *peripheral*, low-mass experts, so features should down-weight the committee (e.g., TF-IDF-like reweighting of expert usage).
- Rating: Medium

### [31] Do Domain-specific Experts exist in MoE-based LLMs? (Giang Do et al., 2026, arXiv 2604.05267)
- URL: https://arxiv.org/abs/2604.05267
- Verified: yes
- Summary: Across ten MoE LLMs (3.8B-120B) finds empirical evidence for domain-aligned experts and proposes training-free Domain Steering MoE (DSMoE) with zero extra inference cost that beats baselines on target and non-target domains.
- Relevance to our hypothesis: Counterpoint to [28]-[30]: domain-aligned routing exists strongly enough to steer with; supports domain-shift detection via routing.
- Rating: Medium

### [32] Multilingual Routing in Mixture-of-Experts (Lucas Bandarkar et al., ICLR 2026, arXiv 2510.04694)
- URL: https://arxiv.org/abs/2510.04694
- Verified: yes
- Summary: Routing is language-specific in early and late decoder layers and cross-lingually aligned in middle layers; per-language performance correlates strongly with routing similarity to English; promoting English-activated middle-layer task experts yields 1-2% gains across 15+ languages, three models, two tasks.
- Relevance to our hypothesis: Routing is a reliable *language* identifier at early/late layers, i.e., an existence proof for input-attribute identification from routing; also motivates layer-wise feature design.
- Rating: Medium

### [33] Probing Semantic Routing in Large Mixture-of-Expert Models (Matthew Lyle Olson et al., 2025, EMNLP 2025 Findings per ACL Anthology listing, arXiv 2502.10928)
- URL: https://arxiv.org/abs/2502.10928
- Verified: yes (arXiv abstract; Anthology page seen in search only)
- Summary: Controlled experiments on >100B-parameter MoEs (same word with different meanings; word substitution in fixed context) show statistically significant semantic routing: expert overlap tracks meaning, not surface form.
- Relevance to our hypothesis: Routing is sensitive to semantics, which is what a task-intent shift changes.
- Rating: Medium

### [34] Understanding and Leveraging the Expert Specialization of Context Faithfulness in Mixture-of-Experts LLMs (Jun Bai et al., EMNLP 2025 Main, arXiv 2508.19594)
- URL: https://arxiv.org/abs/2508.19594
- Verified: yes
- Summary: "Router Lens" identifies experts that handle context faithfulness (they progressively amplify attention to relevant context); CEFT fine-tunes only those experts and matches or beats full fine-tuning.
- Relevance to our hypothesis: Context-following experts are plausibly the ones that light up when an agent starts following *injected* context instead of its system prompt; a candidate feature subset.
- Rating: Medium

### [35] Unveiling and Consulting Core Experts in Retrieval-Augmented MoE-based LLMs (Xin Zhou et al., 2024, arXiv 2410.15438)
- URL: https://arxiv.org/abs/2410.15438
- Verified: yes
- Summary: Finds core expert groups whose activation indicates internal-knowledge sufficiency, retrieved-document quality, and context use in RAG; proposes expert-activation-based strategies to improve RAG.
- Relevance to our hypothesis: Expert activation used as a diagnostic of *how* the model is using retrieved (potentially injected) context; adjacent.
- Rating: Low

### [36] SEUF: Is Unlearning One Expert Enough for Mixture-of-Experts LLMs? (Haomin Zhuang et al., ACL 2025, arXiv 2411.18797)
- URL: https://arxiv.org/abs/2411.18797 (Anthology: https://aclanthology.org/2025.acl-long.424/)
- Verified: yes
- Summary: Expert attribution concentrates unlearning on the expert most engaged by the forget set, with a router anchor loss to stop routing drift; +5% forget quality, +35% utility, 0.06% parameters. (Follow-up: TRACE, arXiv 2606.10338, routing-aware expert calibration, seen in search only.)
- Relevance to our hypothesis: Expert-level intervention machinery; documents that routing shifts uncontrollably under naive fine-tuning, relevant to monitor drift after model updates.
- Rating: Low

### [37] Get my drift? Catching LLM Task Drift with Activation Deltas (TaskTracker) (Sahar Abdelnabi et al., SaTML 2025, arXiv 2406.00799)
- URL: https://arxiv.org/abs/2406.00799
- Verified: yes
- Summary: Adjacent (dense-model) work: detects prompt-injection-induced task drift by a linear probe on the *delta* of residual activations before vs after processing external data; near-perfect ROC AUC on OOD test sets across six models; releases TaskTracker (>500K instances). Not MoE-specific; no routing signals.
- Relevance to our hypothesis: The closest conceptual precursor for "task drift detection from internal signals"; our contribution would be replacing/augmenting hidden-state deltas with already-computed routing trajectories in MoE agents and extending to multi-turn agent traces.
- Rating: High (as adjacent baseline)

### [38] RouteGuard: Internal-Signal Detection of Skill Poisoning in LLM Agents (Wenjie Xiao et al., 2026, arXiv 2604.22888)
- URL: https://arxiv.org/abs/2604.22888
- Verified: yes
- Summary: Adjacent (dense) work: agent skill poisoning causes "attention hijacking"; a frozen-backbone detector fuses response-conditioned attention and hidden-state alignment; 0.8834 F1 on Skill-Inject, recovering 90.51% of description attacks missed by text filters. Despite the name, no MoE routing is involved.
- Relevance to our hypothesis: Same agent-injection threat model and internal-signal philosophy; a natural comparison point for a routing-based detector.
- Rating: Medium

## DIRECT PRIOR WORK

Two papers implement "routing as a runtime detection signal" for LLM safety/quality, and one more comes close:

1. **RouteScan (Lv et al., May 2026, arXiv 2605.24817)** - the closest. Per-layer expert-load histograms (obtained via GPU thread telemetry as a proxy for routing) + 4 layer statistics -> logistic regression -> harmful-prompt AUROC > 0.91 on DeepSeek-V2-Lite, Qwen1.5-MoE, GPT-OSS-20B, OLMoE, with transfer to unseen jailbreak wrappers. Limits: prefill-only, per-prompt, harmfulness (not task deviation), single-turn, no agents, no token-level trajectory.
2. **InnerExpert (Fonseca et al., Aug 2026, arXiv 2608.17687)** - per-token routing signals (router entropy, expert usage, Gini, inverse-Herfindahl, expert disagreement) accumulated during decoding + XGBoost/MLP -> hallucination detection AUROC 0.88-0.91 answer-level, 0.75-0.76 token-level on OLMoE and Gemma-4-26B-A4B. Limits: hallucination target, QA datasets, no adversarial or agent setting.
3. **MASCing (te Lintelo et al., Apr 2026, arXiv 2604.27818)** - LSTM over per-layer routing logits predicts behavior (refusal/compliance) and is used to derive steering masks; evaluated in multi-turn jailbreaks (defense 52.5% -> 83.9%) on seven MoEs. Limits: surrogate used for steering, not reported as a detector; behavior labels are safety, not task adherence.

Nobody has (a) used routing to detect *task deviation / goal hijacking* of an agent, (b) monitored the routing trajectory across turns of an agentic conversation, or (c) combined routing with output-token signals for interception. Queries tried that returned nothing MoE-specific: "mixture of experts prompt injection detection routing", "indirect prompt injection mixture-of-experts routing", "MoE routing task drift / goal hijacking / task deviation", "MoE routing multi-turn conversation task change", "MoE routing system prompt adherence customer service", "expert routing agent tool use / function calling", "expert routing membership inference", "routing MoE OOD Mahalanobis expert features". Semantic Scholar API returned HTTP 429 on all attempts; arXiv API listings (two queries, ~90 entries) surfaced no additional detection papers beyond those above.

## Key takeaways for our project

- Routing is information-rich: expert-selection sequences alone allow 91% top-1 token reconstruction [7], routing weights work as semantic sentence embeddings [8], and coarse expert-load histograms classify prompt attributes at 74-100% on 60-64-expert models [19]. A routing-based task-deviation detector is well-founded, and cheap because routing is already computed.
- Routing is primarily *topic/domain-driven*, not intent-driven [5]: JS divergence for a topic change (~0.34) is 10-30x larger than for refusal-vs-compliance with topic fixed (0.005-0.04). Our reference scenario (support agent -> writing code, or following injected instructions on a new topic) is exactly the case routing should catch; a pure "harmful intent, same topic" case is where routing alone is weak and output tokens must carry the load.
- Harmful-vs-benign is already linearly separable from per-layer expert-load histograms (AUROC > 0.91) in prefill [1]; nobody has done the decode-phase / multi-turn / agent version.
- Per-token routing statistics (router entropy, usage Gini, effective-expert count, expert disagreement) work as runtime signals with a light classifier [2]; router entropy alone reached 0.88 AUROC on Gemma-4-MoE. These are direct feature candidates.
- Sequence models over full per-layer routing logits predict behavior and stay useful in multi-turn adversarial dialogues [3]; routing states at delimiter/boundary tokens encode task context and trajectory history [9].
- Layer choice matters: mid layers are most selective for benign/harmful [6]; early/late layers carry input-attribute (language) identity, middle layers are shared [32]; deep layers can be near-identical across unrelated inputs, especially in reasoning models [28].
- Global expert-set overlap is a poor feature (~60% overlap regardless of question) and prompt-level routing does not predict rollout-level routing [28]; a domain-invariant "standing committee" dominates routing mass [30]. Use distributional/trajectory features, down-weight committee experts, and evaluate on full rollouts.
- Model choice: fine-grained MoEs (OLMoE 64 experts, Qwen3-30B-A3B 128, DeepSeek-V2-Lite 64, GPT-OSS-20B 32) leak far more routing information than Mixtral (8 experts) [19]; the same model set is used by [1], [3], [4], [5], so results will be comparable.
- Threat model: adversaries can steer routing with input-only suffixes [10], [16] or by silencing experts [12], [13]; RouteHijack explicitly proposes pre-softmax router-logit anomaly detection as a defense and reports >25% safety-probability suppression at the boundary token, giving a concrete attack to evaluate a routing monitor against.
- Interception levers already exist: expert (de)activation via router-logit edits [4], [17], and routing enforcement [14]; a detector could trigger these instead of just halting generation.
- Strongest adjacent baseline: TaskTracker activation-delta probes reach near-perfect AUROC for prompt-injection task drift on dense models [37]; we should compare routing features against hidden-state probes on the same MoE models.

## Open gap (what nobody seems to have done)

No paper uses MoE routing (top-k indices / router probabilities per layer per token) to detect that an *agent has deviated from its assigned task* (user-induced task switching or indirect prompt injection), and none monitors the routing trajectory *across turns and across the decode phase* of an agentic conversation. Existing routing-as-signal work is single-turn and prefill-only for harmfulness [1], per-token for hallucination in QA [2], or behavior-steering [3], [4]. There is also no work that (i) builds a "normal-task routing profile" for a deployed agent and scores drift from it online, (ii) fuses routing features with output-token signals for interception, (iii) evaluates routing monitors against routing-aware attacks [10], [15], [16], or (iv) tests whether the topic-driven nature of routing [5] makes routing a *better* task-deviation detector than a harm detector. Those four items define the novelty of the proposed project.
