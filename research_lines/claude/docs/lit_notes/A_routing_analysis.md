# Theme A: What MoE routing encodes

Scope: expert specialization and routing analysis in MoE LLMs -- token/syntax vs. topic/semantics/language, layer-wise structure, effects of fine-grained experts / shared experts / load balancing, routing stability, router interpretability, and how much input information (domain, task, language, intent) is recoverable from routing alone. All entries below were located via web search and their abstract pages (arXiv / ACL Anthology / ICLR / OpenReview) were fetched to confirm title, first author and year. Search date: 2026-09-02.

## Entries

### [1] Mixtral of Experts (Jiang et al., 2024, arXiv)
- URL: https://arxiv.org/abs/2401.04088 (routing analysis: https://arxiv.org/html/2401.04088, Sec. "Routing analysis")
- Verified: yes
- Summary: Mixtral 8x7B (32 layers, 8 experts, top-2). The authors measure expert-selection proportions on Pile subsets (ArXiv, PubMed, PhilPapers, DM Mathematics, GitHub, Gutenberg, StackExchange, Wikipedia) at layers 0, 15, 31 and report "we do not observe obvious patterns in the assignment of experts based on the topic"; only DM Mathematics deviates slightly. Routing instead follows syntax: e.g. "self" in Python and "Question" in English are routed to the same expert across sub-tokens. Strong temporal locality: consecutive tokens share an expert far more than chance at higher layers (e.g. ~28% at layer 15 vs. 12.5% random for the first-choice expert).
- Relevance to our hypothesis: The canonical negative result -- at the level of aggregate expert-usage histograms, an upcycled 8-expert model carries almost no topic signal. This is exactly the "fancy tokenizer" risk. Note the analysis is coarse (histograms over whole documents, 3 layers); it does not test per-token context-conditioned routing or trajectories. Baseline any histogram-based detector against Mixtral-style models to see the floor.
- Rating: High

### [2] OLMoE: Open Mixture-of-Experts Language Models (Muennighoff et al., 2024, arXiv)
- URL: https://arxiv.org/abs/2409.02060 (analysis: https://arxiv.org/html/2409.02060, Sec. "MoE analysis")
- Verified: yes
- Summary: OLMoE-1B-7B (64 experts, top-8, trained from scratch). Defines four routing analyses: (i) router saturation -- ~60% of top-8 routing decisions already match the final checkpoint after 1% (20B tokens) of training and ~80% by 40%; later layers saturate faster, layer 0 is a slow outlier; (ii) expert co-activation -- weak, only small groups of 2-3 experts co-fire in layers 7/15; (iii) domain specialization -- strong: e.g. one layer-0 expert receives nearly 100% of arXiv tokens, GitHub and arXiv share experts at layer 7, while generic C4 is spread uniformly; Mixtral in comparison shows "little domain specialization", attributed to upcycling; (iv) vocabulary specialization -- individual experts concentrate on special characters/non-Latin scripts, punctuation, religious terms, temporal terms, geographic terms.
- Relevance to our hypothesis: Strongly supports the claim that from-scratch, fine-grained (64-expert) MoEs encode domain in routing, and that the topic signal is model-dependent (from-scratch >> upcycled). Also warns that domain specialization is partly confounded with vocabulary (arXiv/GitHub have distinctive tokens). Routing saturates early, so instruction-tuned checkpoints inherit pretraining routing.
- Rating: High

### [3] A Closer Look into Mixture-of-Experts in Large Language Models (Lo et al., 2024, NAACL 2025 Findings)
- URL: https://arxiv.org/abs/2406.18219
- Verified: yes
- Summary: Studies Mixtral 8x7B, DeepSeekMoE, Grok-1 (and Mixtral 8x22B in later version) via parametric (weight similarity) and behavioral (output norms, gate scores) analyses. Findings: routers usually select experts whose outputs have larger norms; individual neurons act as fine-grained experts; expert diversity (weight and output dissimilarity) increases with depth, but the last layer is an outlier that behaves differently.
- Relevance to our hypothesis: Provides a depth profile: experts are most similar (least specialized) in early layers and most diverse in late-middle layers, with the final layer anomalous -- so routing features from the last layer(s) should be treated separately. The output-norm finding suggests router logits partially encode activation magnitude, not just content.
- Rating: Medium

### [4] ST-MoE: Designing Stable and Transferable Sparse Expert Models (Zoph et al., 2022, arXiv)
- URL: https://arxiv.org/abs/2202.08906 (qualitative analysis in Sec. "Tracing tokens through the model", https://ar5iv.labs.arxiv.org/html/2202.08906)
- Verified: yes
- Summary: Encoder-decoder MoE (269B). Encoder experts show clear shallow specialization: "at each layer, at least one expert specializes in sentinel tokens", plus experts for punctuation, conjunctions/articles, verbs, proper names, counting/numbers, visual descriptions. "Expert specialization is far less noticeable in the decoder." In the multilingual model, "we find no evidence of language specialization" -- load balancing forces every expert to handle all languages.
- Relevance to our hypothesis: Foundational evidence that (a) routing captures token-class / syntactic categories, and (b) load balancing can actively suppress high-level (language/topic) specialization. Decoder-only LLMs may behave like the "decoder" here (weaker, noisier specialization).
- Rating: Medium

### [5] Towards an empirical understanding of MoE design choices (Fan, Messmer & Jaggi, 2024, arXiv)
- URL: https://arxiv.org/abs/2402.13089
- Verified: yes
- Summary: Controlled small-scale ablations of MoE design choices. Learned routers perform comparably to frozen random routers in some settings. Routing granularity determines what experts specialize on: token-level routing yields syntax-based specialization; sequence-level routing yields "topic-specific weak expert specialization".
- Relevance to our hypothesis: Directly frames the syntax-vs-topic question as a consequence of routing granularity. Production LLMs use token-level routing, so per-token routing is expected to be syntax-dominated; topic signal, if present, must come from context leaking into hidden states -- which motivates pooling routing over a window/sequence rather than per token.
- Rating: Medium

### [6] Routing in Sparsely-gated Language Models responds to Context (Arnold, Fietta & Yesilbas, 2024, arXiv)
- URL: https://arxiv.org/abs/2409.14107
- Verified: yes
- Summary: Traces routing on similarity-annotated text pairs to test whether token-expert assignments are context-sensitive, building on earlier evidence that assignments are "predominantly influenced by token identities and positions". Finds encoder-layer routing depends mainly on (semantic) associations with contextual refinement, while decoder-layer routing is "more variable and markedly less sensitive to context".
- Relevance to our hypothesis: A caution: in decoder layers (the only kind in modern LLMs) context sensitivity of routing is weak in the small models studied. Any deviation detector must show it beats a token-identity baseline.
- Rating: Medium

### [7] Part-Of-Speech Sensitivity of Routers in Mixture of Experts Models (Antoine, Bechet & Langlais, 2025, COLING 2025)
- URL: https://aclanthology.org/2025.coling-main.431/ (arXiv: https://arxiv.org/abs/2412.16971)
- Verified: yes
- Summary: Six models (dbrx-base, Mixtral-8x7B, Phi-3.5-MoE, deepseek-moe-16b, Qwen1.5-MoE-A2.7B, OLMoE-1B-7B). MLPs trained on the sequence of expert IDs across layers predict POS tags with 0.75-0.89 accuracy (OLMoE 0.75, Mixtral 0.84, Phi-3.5 0.89) vs. a most-frequent-POS-per-word-form baseline of 0.91. Experts are POS-specialized above uniform (SpecPOS +25% Mixtral to +36% OLMoE); punctuation is up to 84.5% concentrated in a few experts (Phi-3.5) vs. 12.5% uniform. Removing early-layer routing hurts POS prediction more than removing late layers.
- Relevance to our hypothesis: The clearest quantification of the "routing = syntax" concern: routing paths are strongly predictive of POS, but notably slightly less than the word-form baseline, i.e. routing is not a pure token-id lookup. Early layers carry syntax; late layers carry something else -- a hint that task/context signal should be sought in middle/late layers. Their probe design (MLP on concatenated per-layer expert IDs) is a directly reusable template.
- Rating: High

### [8] Probing Semantic Routing in Large Mixture-of-Expert Models (Olson et al., 2025, EMNLP 2025 Findings; v1 titled "Semantic Specialization in MoE Appears with Scale: A Study of DeepSeek R1 Expert Specialization")
- URL: https://arxiv.org/abs/2502.10928 (v1: https://arxiv.org/abs/2502.10928v1; ACL: https://aclanthology.org/2025.findings-emnlp.991.pdf)
- Verified: yes
- Summary: Controlled experiments on >100B MoEs (DeepSeek-R1 and others): (a) same target word used in different senses in two sentences; (b) substituting the target word with semantically near vs. far alternatives while holding context fixed. Expert overlap is lower when senses differ, statistically significant (p < 0.001) for all models when averaged across layers. v1 additionally used a DiscoveryWorld cognitive-reasoning analysis and argued semantic routing "appears with scale".
- Relevance to our hypothesis: Direct evidence that routing of the *same token* changes with meaning/context in large modern MoEs -- routing is more than token identity. Supports the idea that a task/context shift will shift routing even when surface tokens are similar. Effect sizes are modest and layer-averaged; per-layer effects should be checked.
- Rating: High

### [9] Your Mixture-of-Experts LLM Is Secretly an Embedding Model For Free (Li & Zhou, 2024, arXiv)
- URL: https://arxiv.org/abs/2410.10814
- Verified: yes
- Summary: Uses router weights (RW: per-layer routing probabilities concatenated) of MoE LLMs as sentence embeddings without fine-tuning. On 6 MTEB task types / 20 datasets, RW is complementary to hidden states (HS); RW is "more robust to the choice of prompts and focuses on high-level semantics", and a weighted sum of RW and HS similarities (MoEE) beats either alone.
- Relevance to our hypothesis: The strongest positive evidence that aggregated routing probabilities carry high-level semantic / topical information about the input, enough to do STS, clustering and classification. This is essentially the "information recoverable from routing alone" question answered affirmatively at sequence level. Their RW embedding is a ready-made feature vector for a deviation detector.
- Rating: High

### [10] Multilingual Routing in Mixture-of-Experts (Bandarkar et al., 2025, ICLR 2026)
- URL: https://arxiv.org/abs/2510.04694
- Verified: yes
- Summary: Using parallel multilingual data across several open MoEs, shows routing is language-specific in early and late decoder layers but strongly cross-lingually aligned in middle layers. A language's benchmark performance correlates with how similarly its tokens are routed to English in middle layers. Steering middle-layer routers toward English-task experts gives consistent 1-2% gains across 15+ languages and 3 models. Also notes routing entropy decreases with depth.
- Relevance to our hypothesis: Establishes the now-recurring layer profile: early/late layers = surface (language, token form), middle layers = language-agnostic, task-related "hub" experts. If task/intent signal lives anywhere, it is in middle layers; surface-level features dominate the ends.
- Rating: High

### [11] Understanding Multilingualism in Mixture-of-Experts LLMs: Routing Mechanism, Expert Specialization, and Layerwise Steering (Chen et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2601.14050
- Verified: yes
- Summary: Routing aligns with linguistic families; high-resource languages rely on shared experts, low-resource languages on language-exclusive experts; "early and late MoE layers support language-specific processing, whereas middle layers serve as language-agnostic capacity hubs." Routing-guided steering of middle layers improves multilingual performance.
- Relevance to our hypothesis: Independent replication of the early/late-surface vs. middle-abstract layer profile, with routing entropy per language as a diagnostic (low-resource languages have lower entropy / more concentrated routing).
- Rating: Medium

### [12] Ban&Pick: Enhancing Performance and Efficiency of MoE-LLMs via Smarter Routing (Chen et al., 2025, EMNLP 2026)
- URL: https://arxiv.org/abs/2509.06346 (analysis: https://arxiv.org/html/2509.06346)
- Verified: yes
- Summary: On Qwen3-30B-A3B (128 experts, top-8), measures expert usage frequency per task. In each layer some experts fire up to 6x the average, and which experts are hot differs by task: L16E95 is active almost only on math (digits, operators, math terms), L29E91 on code with 66.9% activation rate, while L4E48 is hot on all tasks (branch/reasoning tokens). Reinforcing key experts raises AIME24 from 80.67 to 84.66.
- Relevance to our hypothesis: Concrete evidence that a modern fine-grained MoE has task-specific (math vs. code) experts in middle/late layers -- exactly the "customer-service agent starts writing code" scenario: a code-expert like L29E91 lighting up would be a strong, cheap deviation signal. The caveat remains that the tokens themselves are code tokens.
- Rating: Medium

### [13] What Gets Activated: Uncovering Domain and Driver Experts in MoE Language Models (Hu et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2601.10159
- Verified: yes
- Summary: Across three MoE LLMs and three domains, defines "domain experts" via an entropy-based preference metric on routing and "driver experts" via a causal-effect metric on outputs. Domain-preferring experts and causally-important experts are largely different sets; driver experts are triggered more by earlier tokens in a sentence. Re-weighting domain/driver experts improves accuracy by 2.08% / 3.00% on average.
- Relevance to our hypothesis: Provides a routing-only entropy metric for domain preference that can be computed at runtime, and the warning that domain-preferring experts need not be the functionally important ones (so routing-based domain signals need not correlate with behavior).
- Rating: Medium

### [14] The Myth of Expert Specialization in MoEs: Why Routing Reflects Geometry, Not Necessarily Domain Expertise (Wang, Hayou & Nalisnick, 2026, arXiv)
- URL: https://arxiv.org/abs/2604.09780
- Verified: yes
- Summary: Since routers are linear maps, hidden-state similarity is necessary and sufficient for expert-usage similarity; load-balancing loss keeps routing diverse by suppressing shared hidden-state directions. Empirically (GPT-OSS-20B, ERNIE-4.5-21B, Qwen3-30B, Ling-mini, Trinity-Mini; Jaccard@top-p=0.8 on expert usage): two different models solving the same HMMT problem share only ~60% experts, the same as one model solving different problems; in GPT-OSS-20B's last layers during prefill "semantically unrelated sequences can activate exactly the same experts" (pruning the last two layers' experts costs <10% NLL on prompt-only input); and prompt-level (prefill) routing does not predict generation-time routing.
- Relevance to our hypothesis: The most important recent caution. (a) Routing is a linear readout of hidden-state geometry -- so a routing detector is a cheap, low-rank probe of the residual stream, no more and no less. (b) Prefill routing on the prompt is weakly informative; the signal appears during generation -- so monitor routing on generated tokens, not just the prompt. (c) Late layers can collapse to near-identical routing across topics during prefill -- exclude/handle them.
- Rating: High

### [15] Layer-wise MoE Routing Locality under Shared-Prefix Code Generation: Token-Identity Decomposition and Compile-Equivalent Fork Redundancy (Hayashi et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2604.17182
- Verified: yes
- Summary: Qwen3.5-35B-A3B (256 experts, top-8), 851 sampled code completions from a shared prefix. Decomposes routing similarity by token identity per layer: identical tokens have Jaccard 0.649 (40x random) and different tokens 0.175 (11x random); in the input layer same-token similarity is 0.83, while in middle layers (L14-20) same-token similarity dips and different-token similarity peaks (14x random), i.e. context-driven routing dominates in the middle and token identity dominates at the input.
- Relevance to our hypothesis: Gives a quantitative per-layer "how much is token identity" decomposition on a current model: early layers are near token-lookups, middle layers are contextual. Actionable: fit the detector on middle layers and always report a token-identity-matched control.
- Rating: High

### [16] Does the Same Token Mean the Same State? MoE Routing as Signal for Reasoning Control (Chen et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2606.22798
- Verified: yes
- Summary: Tests whether the same token id implies the same router state in gpt-oss and Qwen3-MoE (10 configurations, 6 datasets). Finds it does not: routing for identical tokens differentiates task context, trajectory history and reasoning effort. Proposes Routing-Aware Decoding, selecting among samples via "routing neighborhoods" at delimiter anchors (e.g. code fences) without parsing answers: 73.9% (74.2% with dense clustering) vs. 73.6% majority vote, with gains on code pass@1 and SWE-bench patch selection.
- Relevance to our hypothesis: Closest in spirit: uses routing (not outputs) as a runtime signal of the model's latent state on modern MoEs, and explicitly shows routing at fixed tokens encodes task context and trajectory history. Their anchor-token trick (compare routing at structurally identical tokens across contexts) neatly controls the token-identity confound.
- Rating: High

### [17] Polysemantic Experts, Monosemantic Paths: Routing as Control in MoEs (Ye, Yuan & Sharkey, 2026, arXiv)
- URL: https://arxiv.org/abs/2604.17837
- Verified: yes
- Summary: Parameter-free decomposition of each layer's hidden state into a "control" subspace that causally drives routing and an orthogonal "content" channel invisible to the router, across six MoE architectures. Surface features (language, token identity, position) live in the content channel, while the control signal encodes an abstract function that rotates layer to layer. Individual experts are polysemantic, but token trajectories through experts are far more monosemantic: e.g. a colon takes different paths as type annotation, list introducer, or time separator.
- Relevance to our hypothesis: Argues that the natural interpretable unit is the routing *trajectory* across layers, not single experts -- exactly what we propose to monitor. Also claims surface features are partly orthogonal to what the router reads, which cuts against the "router = tokenizer" worry.
- Rating: High

### [18] The Expert Strikes Back: Interpreting Mixture-of-Experts Language Models at Expert Level (Herbst, Wermter & Lee, 2026, arXiv)
- URL: https://arxiv.org/abs/2604.02178
- Verified: yes
- Summary: k-sparse probing shows expert neurons are consistently less polysemantic than dense-FFN neurons, with the gap widening as routing gets sparser. Auto-interpreting hundreds of experts, they find experts are neither broad domain specialists nor mere token processors but "fine-grained task experts, specializing in linguistic operations or semantic tasks (e.g., closing brackets in LaTeX)".
- Relevance to our hypothesis: Expert-level meaning sits between syntax and topic: fine-grained operations. A task switch (dialogue -> code) would change which operation-experts fire, but so would a topic-preserving format change; the detector should be validated against format-only shifts.
- Rating: Medium

### [19] RouterInterp: Understanding Superposed Specialisation in MoE Routing (Lasy, Cai & Ayonrinde, 2026, ICLR 2026 Sci4DL workshop)
- URL: https://iclr.cc/virtual/2026/10016067 (OpenReview: https://openreview.net/forum?id=9a5i2vyMwN)
- Verified: yes
- Summary: Proposes the Superposed Specialisation Hypothesis: each expert specializes in a disjoint union of fine-grained SAE features rather than one broad domain. RouterInterp finds the SAE latents most predictive of routing and aggregates natural-language explanations per expert; on gpt-oss-20b its explanations predict routing 77% more accurately than prior methods.
- Relevance to our hypothesis: Explains why simple "expert = topic" readings fail while routing can still be predicted from semantic features: the mapping is many-to-one. Suggests a detector should operate on distributions over many experts/layers (superposed features), not on a few named experts.
- Rating: Medium

### [20] Safety-Oriented Routing Analysis of Mixtral MoE Under Benign and Harmful Prompts (Siddiky, 2026, arXiv)
- URL: https://arxiv.org/abs/2605.24270
- Verified: yes
- Summary: Mixtral-8x7B-Instruct; compares expert-selection frequencies (activation-based) and gradient-based expert importance for benign vs. harmful prompts. Separation at the expert level is modest and most experts are shared; activation-based routing is most selective in layers 8-15, gradient importance concentrates in final layers. Suppressing the top-5 benign-dominant experts cut restricted responses from 24 to 14 of 100. Conclusion: safety-relevant routing is "subtle, depth-dependent, and distributed".
- Relevance to our hypothesis: A near-direct test of "does routing separate intended vs. unintended prompts" on Mixtral: yes but weakly and mainly in middle layers (8-15). Useful as a negative-ish baseline and for the layer range.
- Rating: High

### [21] RouteScan: A Non-Intrusive Approach to Auditing MoE LLMs Safety via Expert Routing Telemetry (Lv et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2605.24817
- Verified: yes
- Summary: Detects harmful prompts from routing-induced GPU telemetry (active thread counts per expert during prefill), i.e. aggregate expert-load patterns plus layer-level statistics, with a hybrid feature-scoring step to pick features stable across harmful domains and jailbreak wrappers. On four open MoE LLMs achieves AUROC > 0.91 on unseen harmful domains; privacy tests show aggregated telemetry leaks input attributes but not full prompts.
- Relevance to our hypothesis: Proof of concept that aggregate expert-load statistics alone support a harmful-vs-benign classifier that generalizes across domains and jailbreak wrappers (i.e. not purely lexical). Differences from our setting: prefill-only, prompt-level, safety labels rather than task-deviation labels, and the "Myth" paper's finding that prefill routing under-determines generation.
- Rating: High

### [22] RASET: Router-Agnostic Safety-Critical Expert Tuning Exposes Localized Safety Enforcement Failures in MoE LLMs (Zhang et al., 2026, EMNLP 2026; earlier title "Understanding Safety-Sensitive Expert Behavior in Mixture-of-Experts LLMs")
- URL: https://arxiv.org/abs/2605.29708
- Verified: yes
- Summary: Across five open-weight aligned MoEs, shows "routing patterns in aligned MoE LLMs are largely topic-driven, while safety behavior can be altered with little change to the model's intrinsic routing path"; tuning a few safety-critical experts yields 50.5% high-quality attack success (+37.6 pts over the best baseline) while routing is preserved.
- Relevance to our hypothesis: Double-edged. Supports that routing tracks topic/semantics (good for detecting a topic/task switch), but shows routing is insensitive to behavioral changes that keep the topic fixed (refusal vs. compliance). So routing may detect "the agent is now doing something else" but not "the agent is doing the same thing wrongly".
- Rating: High

### [23] Steering MoE LLMs via Expert (De)Activation (Fayyaz et al., 2026, ICLR 2026)
- URL: https://arxiv.org/abs/2509.09660
- Verified: yes
- Summary: SteerMoE identifies behavior-linked experts by contrasting expert activation frequencies on paired inputs with opposite behaviors (safe/unsafe, faithful/unfaithful), then (de)activates them at test time; on 11 benchmarks and 6 LLMs safety improves up to +20% and faithfulness +27%, while deactivation reduces safety by 41% (100% combined with jailbreaks).
- Relevance to our hypothesis: The contrastive-frequency method is a simple recipe for finding experts whose routing frequency differs between "on-task" and "off-task" traces; the fact that such experts exist and are causally effective indicates routing frequencies encode behavior-relevant state (in tension with [22], which is about the intrinsic path being preserved after tuning).
- Rating: Medium

### [24] Routers Learn the Geometry of Their Experts: Geometric Coupling in Sparse Mixture-of-Experts (Ahrac, Hochwald & Geva, 2026, arXiv)
- URL: https://arxiv.org/abs/2605.12476
- Verified: yes
- Summary: Router and expert weight gradients align along the same input directions, so router scores track expert-neuron activation strength (shown in a 1B SMoE). Auxiliary load-balancing loss spreads gradients and makes router directions ~3x more similar to each other, weakening this coupling; a parameter-free K-means router (running means of routed hidden states) balances load with modest perplexity cost.
- Relevance to our hypothesis: Mechanistic account of why router logits are a readout of "which expert's features are present"; and why load balancing blurs router directions (less discriminative routing). Models trained with aux-loss-free balancing (DeepSeek-V3 style) may have sharper routing signals.
- Rating: Medium

### [25] Advancing Expert Specialization for Better MoE (Guo et al., 2025, NeurIPS 2025 oral)
- URL: https://arxiv.org/abs/2505.22323
- Verified: yes
- Summary: Shows the standard auxiliary load-balancing loss "leads to expert overlap and overly uniform routing, which hinders expert specialization"; adds an orthogonality loss (experts process distinct token types) and a variance loss (more decisive routing); up to 23.79% improvement over aux-loss baselines while keeping balance.
- Relevance to our hypothesis: Load-balancing is a confound for specialization: how much topic signal routing carries depends on how the model was balanced. Expect weaker signals in heavily aux-loss-balanced models and stronger ones in aux-loss-free / fine-grained models.
- Rating: Medium

### [26] MoE Routing Testbed: Studying Expert Specialization and Routing Behavior at Small Scale (Falke et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2604.07030
- Verified: yes
- Summary: A testbed pairing a data mix with clearly separable domains and a reference router prescribing ideal domain routing, giving an upper bound to measure specialization. Comparing routing methods, "balancing scope is the crucial factor that allows specialization while maintaining high expert utilization"; results generalize to models 35x larger.
- Relevance to our hypothesis: Offers a way to quantify "fraction of ideal domain routing achieved" -- a useful upper-bound framing for how much domain information a router can carry, and confirms that the balancing scheme (batch vs. global scope) governs it.
- Rating: Medium

### [27] DBES: A Systematic Benchmark and Metric Suite for Evaluating Expert Specialization in Large-Scale MoEs (Wang et al., 2026, arXiv)
- URL: https://arxiv.org/abs/2605.18498
- Verified: yes
- Summary: Multi-domain benchmark plus five metrics: routing specialization, normalized effective rank, domain isolation, routing stiffness (consistency), n-gram expertise. Finds architecture-dependent regimes: Qwen-series MoEs show modular, high domain-isolation specialization; DeepSeek and GLM show distributed collaboration. Specialization is "necessary but insufficient" for downstream performance.
- Relevance to our hypothesis: Provides off-the-shelf metrics (domain isolation, routing stiffness) and the important point that the amount of domain signal in routing differs by model family -- Qwen3-MoE may be a much better host for a routing detector than DeepSeek/GLM.
- Rating: Medium

### Additional verified, lower-relevance references
- DeepSeekMoE: Towards Ultimate Expert Specialization (Dai et al., 2024, ACL 2024) -- https://arxiv.org/abs/2401.06066 -- introduces fine-grained expert segmentation and shared-expert isolation explicitly to increase routed-expert specialization; architectural motivation for why fine-grained models (DeepSeek-V3, Qwen3-MoE) should carry more domain signal. Verified: yes. Rating: Medium.
- Monet: Mixture of Monosemantic Experts for Transformers (Park et al., 2024, ICLR 2025) -- https://arxiv.org/abs/2412.04139 -- 262k experts/layer; shows mutually exclusive domain, language and toxicity knowledge across experts, i.e. routing-level interpretability is achievable when experts are extremely fine-grained. Verified: yes. Rating: Medium.
- Towards Understanding Mixture of Experts in Deep Learning (Chen et al., 2022, NeurIPS 2022) -- https://arxiv.org/abs/2208.02813 -- theory: routers learn cluster-center features of the data; specialization requires cluster structure and non-linear experts. Verified: yes. Rating: Low.
- Unveiling Language Routing Isolation in Multilingual MoE Models (Zheng et al., 2026) -- https://arxiv.org/abs/2604.03592 -- high- and low-resource languages activate largely disjoint expert sets; layer-wise convergence-divergence pattern; language-specific experts in shallow/deep layers, shared in middle. Verified: yes. Rating: Low.
- FLAME-MoE (Kang, Yu & Xiong, 2025) -- https://arxiv.org/abs/2505.20225 -- open platform; confirms experts increasingly specialize on distinct token subsets, routing stabilizes early, co-activation stays sparse. Verified: yes. Rating: Low.
- Cosine-Similarity Routing with Semantic Anchors (Ternovtsii & Bilak, 2025) -- https://arxiv.org/abs/2509.14255 -- estimates 44-54% of expert specialization in their small models is syntactic rather than semantic. Verified: yes. Rating: Low.
- Sparsity and Superposition in Mixture of Experts (Chaudhari et al., 2025) -- https://arxiv.org/abs/2510.23671 -- greater network sparsity yields more monosemantic experts (toy setting). Verified: yes. Rating: Low.
- When Are Experts Misrouted? Counterfactual Routing Analysis (Yoon et al., 2026) -- https://arxiv.org/abs/2605.07260 -- router confidence (logit margin) predicts when the selected route is good; low-confidence tokens are where routing fails (Qwen3-30B-A3B, GPT-OSS-20B, DeepSeek-V2-Lite, OLMoE). Verified: yes. Rating: Low.
- Stealing User Prompts from Mixture of Experts (Yona et al., 2024) -- https://arxiv.org/abs/2410.22884 -- side channel via expert-choice capacity ties leaks tokens; shows routing decisions carry input information, though via a different mechanism. Verified: yes. Rating: Low.
- A theoretical model for task routing in mixture-of-expert transformers (Nandakumar et al., 2026) -- https://arxiv.org/abs/2606.14398 -- CE / load-balancing training yields diffuse, task-agnostic routing; explicit task-based routing gives clean task-expert alignment at equal accuracy. Verified: yes. Rating: Low.

## Key takeaways for our project
- Per-token routing is dominated by surface form in the first layer(s): same-token routing similarity ~0.83 at the input layer (Hayashi 2026), POS is predictable from routing paths at 0.75-0.89 (Antoine 2025), and Mixtral-style upcycled 8-expert models show essentially no topic signal in usage histograms (Jiang 2024; Muennighoff 2024 comparison). A detector built on early-layer or histogram-only features is at real risk of being a tokenizer-level detector.
- Context and task signal concentrates in the middle layers: different-token routing similarity peaks at L14-20 in Qwen3.5-35B (Hayashi 2026); language-agnostic "hub" experts sit in middle layers (Bandarkar 2025; Chen 2026); Mixtral safety-relevant routing is most selective at layers 8-15 (Siddiky 2026). Late layers can collapse to near-identical routing across unrelated prompts during prefill (Wang 2026) and the last layer is an outlier (Lo 2024). Actionable: build features from middle layers, treat last 1-2 layers separately.
- Routing is more than token identity in large modern MoEs: the same word in different senses routes differently (Olson 2025, p<0.001), the same token id maps to different router states depending on task context and trajectory (Chen 2026 "Same Token"), and colons/brackets take different paths by function (Ye 2026). This is the positive evidence our hypothesis needs.
- Sequence-level aggregation rescues topic information: pooled router weights work as semantic embeddings on MTEB (Li & Zhou 2024); sequence-level routing yields topic specialization while token-level yields syntax (Fan 2024); aggregated expert-load statistics separate harmful from benign prompts at AUROC>0.91 across domains (Lv 2026). Use windowed / pooled routing distributions, not single-token routing.
- The topic signal is strongly model-dependent: from-scratch fine-grained models (OLMoE, Qwen3-MoE) show sharp domain/task experts (e.g. Qwen3-30B L29E91 fires at 66.9% on code; Chen 2025), while upcycled Mixtral and "distributed" families (DeepSeek, GLM per DBES) show weak isolation. Load-balancing loss blurs router directions (Ahrac 2026; Guo 2025; Zoph 2022). Choose Qwen3-MoE / OLMoE / gpt-oss as first hosts and expect weaker results on Mixtral.
- Prefill routing on the prompt under-determines what happens during generation (Wang 2026). Our use-case (agent talked into deviating mid-trajectory) should monitor routing on generated tokens and on the injected content as it is processed, not only on the initial prompt.
- Routing tracks topic more than behavior: aligned MoEs keep their routing path even when safety behavior is flipped by expert tuning (Zhang 2026), yet contrastive expert frequencies do identify behavior-linked experts (Fayyaz 2026). Expect routing to detect "task/topic changed" reliably, and "same task, wrong intent" only weakly.
- Controls to include: token-identity-matched baselines (compare routing at identical anchor tokens across on-task vs. off-task contexts, as in Chen 2026 and Hayashi 2026), a most-frequent-expert-per-token lookup baseline (analogous to the 0.91 word-form POS baseline), and format-only shifts (e.g. markdown/JSON) to rule out syntax-driven false positives.
- Routing is stable across training (60% of top-8 decisions fixed after 1% of pretraining, ~80% by 40%; Muennighoff 2024), so a detector calibrated on a base model likely transfers to its instruction-tuned sibling, and router logit margins (Yoon 2026) offer a confidence feature.
- Mechanistically, router logits are a linear readout of the residual stream (Wang 2026; Ahrac 2026): a routing-based detector is a free, low-rank linear probe. Its ceiling is bounded by a linear probe on hidden states, but it is already computed and much cheaper to log.

## Closest prior work
- [16] Chen et al. 2026, "Does the Same Token Mean the Same State?" -- the only work found that uses MoE routing as a runtime signal of latent state on current models (gpt-oss, Qwen3-MoE), explicitly showing routing at a fixed token encodes task context and trajectory history; their anchor-token comparison is the right control for the token-identity confound.
- [21] Lv et al. 2026, RouteScan, together with [20] Siddiky 2026 -- routing-statistics-only classifiers for benign vs. harmful prompts (AUROC>0.91 across four MoEs; Mixtral layers 8-15 most selective). Closest to a deployed "intercept from routing" system, but prompt/prefill-level and safety-labelled rather than task-deviation-labelled.
- [14] Wang, Hayou & Nalisnick 2026, "The Myth of Expert Specialization", plus [7] Antoine 2025 and [15] Hayashi 2026 -- the strongest formulations of the null hypothesis we must beat (geometry/token identity, prefill collapse), and the source of the per-layer decomposition method we should replicate.

## Open gap
- Nobody has measured routing-trajectory deviation for *task/role drift* in an agentic, multi-turn setting: existing routing-as-signal work is either prompt-level safety classification (RouteScan, Siddiky), reasoning-sample selection (Chen 2026), or steering (SteerMoE). There is no study of routing during a customer-service style trajectory where the task is redirected (user-induced or indirect prompt injection), no comparison of routing on the injected text vs. the model's own generated tokens, and no analysis of whether deviation shows up before the output tokens reveal it (lead time).
- No work quantifies how much of a routing-based domain/task classifier's accuracy survives after conditioning on token identity (the 0.91 word-form vs. 0.75-0.89 routing-path POS numbers are the closest), nor separates "topic shift" from "format shift" (prose -> code) as confounds -- the key experiment for our "fancy tokenizer" risk.
- Routing changes under post-training (SFT/RLHF) and across turns/roles (system vs. user vs. tool messages) are essentially unstudied; the layer profile (early = surface, middle = task, late = collapsed) is well replicated for language and domain but not for task intent in instruction-tuned models.
