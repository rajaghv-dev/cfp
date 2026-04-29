# Model Evals — Open-Source Code/Reasoning LLMs for 16 GB VRAM

> Research log: which models can run on this machine (RTX 3080 Ti Laptop, 16 GB VRAM),
> ranked by published benchmarks. Generated 2026-04-29.
> Re-run this research if more than 3 months old — model landscape shifts fast.

---

## 1. Hardware Constraint

| | |
|---|---|
| GPU | NVIDIA GeForce RTX 3080 Ti Laptop |
| VRAM | 16 GB (16384 MiB) |
| Driver / CUDA | 581.95 / CUDA 13.0 |
| Container access | Confirmed (`docker run --gpus all`) |
| Effective VRAM ceiling for inference | ~14 GB (leave 2 GB for KV cache + overhead) |

**Rule of thumb for fit**: at Q4_K_M, model file size ≈ params(B) × 0.55 GB. Add 10–20% runtime overhead. A 24B model at Q4_K_M ≈ 13–15 GB on disk and just barely runs in 16 GB VRAM at small context.

---

## 2. Research Methodology

### 2.1 Web searches performed

| # | Query | Purpose |
|---|---|---|
| 1 | `best open source code LLM models 2026 benchmark SWE-bench LiveCodeBench leaderboard` | General code LLM landscape |
| 2 | `ollama latest code models 2026 qwen3 deepseek devstral available` | What's actually on Ollama registry |
| 3 | `FPGA RTL Verilog VHDL HLS LLM model 2025 2026 VerilogEval benchmark` | Hardware-design specialists |
| 4 | `devstral small 24b ollama pull size VRAM 2026` | Devstral specifics for 16 GB fit check |
| 5 | `kimi k2.5 GLM-5 qwen3-coder ollama pull size parameters 16GB 2026` | Verify the "top SWE-bench" models can't fit |

### 2.2 Pages fetched (full content extracted)

| URL | What we extracted |
|---|---|
| <https://ollama.com/search?q=coder> | Authoritative list of code models actually on Ollama, with size tags |
| <https://onyx.app/insights/best-llms-for-coding-2026> | SWE-bench / LiveCodeBench / HumanEval scores for top open-weight models |
| <https://iprc-dip.github.io/CodeV-R1/> | CodeV-R1 details (size, base model, Verilog scores, availability) |

### 2.3 Sources also surfaced (not deeply fetched, used in synthesis)

- BenchLM.ai open-weight leaderboard
- Vellum LLM leaderboard 2026
- ResBench (FPGA-aware Verilog benchmark, 2025)
- HLS-Eval (April 2025 — first complete HLS benchmark)
- Mod-VerilogEval v2 (rectified VerilogEval v2)
- ProtocolLLM (SystemVerilog benchmark with PPA metrics)
- CodeV-R1 paper
- RTLCoder GitHub (HKUST-Zhiyao)
- Kimi K2.6 blog
- Unsloth docs on Kimi K2.5 / K2.6 local-run guides
- Devstral Small 2 page on Ollama / HuggingFace card

---

## 3. Raw Findings — Top of the Open-Weight Leaderboards (as of early 2026)

These are the **state-of-the-art open-weight** models. **None of them fit on 16 GB.** Documenting so we don't waste cycles considering them.

| Model | Vendor | License | SWE-bench Verified | HumanEval | LiveCodeBench | Why it doesn't fit |
|---|---|---|---|---|---|---|
| DeepSeek V4 Pro (Max) | DeepSeek | Open weight | **80.6** | — | **93.5** | 671B+ MoE; needs server-class hardware |
| GLM-5 | Zhipu AI | MIT | 77.8 | 90.0 | 52.0 | 200B+ MoE; ≥80 GB minimum |
| Kimi K2.5 | Moonshot | MIT | 76.8 | **99.0** | 85.0 | **Full Ollama pull = 623 GB** |
| Qwen 3.5 (Reasoning) 397B | Alibaba | Apache 2.0 | 76.4 | — | 83.6 | 397B MoE |
| MiMo-V2-Flash | Xiaomi | MIT | 73.4 | 84.8 | 80.6 | Large MoE |
| GLM-4.7 | Zhipu AI | MIT | — | — | 84.9 | 200K context, large |

**Source for this table**: [Best LLMs for Coding 2026 — Onyx AI](https://onyx.app/insights/best-llms-for-coding-2026) and [BenchLM.ai leaderboard](https://benchlm.ai/blog/posts/best-open-source-llm).

**Translation:** anyone telling you "just run GLM-5 / Kimi locally" on a 16 GB GPU is wrong. The Kimi K2.5 GGUF with usable quant alone exceeds 100 GB.

---

## 4. Code Models that DO Fit on 16 GB

Sorted by best-evidence fit. Where two sources gave conflicting scores I flag the conflict (`⚠ conflict`).

### 4.1 General coding

| Model | Ollama tag | File size | SWE-bench | HumanEval | MBPP+ | LiveCodeBench | Notes |
|---|---|---|---|---|---|---|---|
| **Devstral Small 2** | `devstral-small-2:24b` | **15 GB** at Q4 | ~50% (agent mode, vendor) | — | — | — | Purpose-built for agentic coding (multi-file edits, tool use). Tightest fit of any "modern" coding model. Limited context at 16 GB — long-context variants need 27–35 GB. |
| **DeepSeek-Coder-V2 Lite** | `deepseek-coder-v2:16b` | ~9 GB | 12.7% | 90.2% | 76.2% | 43.4% | 16B MoE with ~8B active per token. Long-context (128K). First open-source model to break 10% on SWE-bench. |
| **Qwen2.5-Coder 14B** | `qwen2.5-coder:14b` | ~10 GB | — | ⚠ conflict: 64.0% (base paper), ~89% (instruct variant per blog) | 66.7 (base) | — | Strong dense coder; Apache 2.0. Verify which variant is on Ollama before reporting numbers. |
| **Qwen2.5-Coder 7B** | `qwen2.5-coder:7b` | ~5 GB | — | ⚠ conflict: 61.6% (base) vs ~88% (instruct) | 62.9 (base) | — | Cheap autocomplete tier. |
| **Qwen3-Coder 30B** | `qwen3-coder:30b` | ~16–17 GB at Q4 | — | — | — | — | Borderline; needs Q3 to fit reliably in 16 GB. Better coding than 14B but limited context. |
| **Codestral 22B** | `codestral:22b` | ~12 GB | — | — | — | — | Best open FIM (fill-in-the-middle) for autocomplete; broadest language coverage including VHDL, TCL, HLS pragmas. Mistral non-commercial license — check if applicable. |
| **CodeGemma 7B** | `codegemma:7b` | ~5 GB | — | 60.4% (Python) | — | — | Lightweight; Google. |
| **StarCoder2 15B** | `starcoder2:15b` | ~9 GB | — | 72.6% | — | — | BigCode. Verilog/VHDL in training data. |
| **DeepCoder 14B** | `deepcoder:14b` | ~9 GB | — | — | — | — | Less data published; verify before trusting. |
| **Yi-Coder 9B** | `yi-coder:9b` | ~6 GB | — | — | — | — | 01.AI. |
| **OpenCoder 8B** | `opencoder:8b` | ~5 GB | — | — | — | — | Fully open training data. |

### 4.2 Reasoning / chain-of-thought (for debugging and dedup)

| Model | Ollama tag | File size | Notes |
|---|---|---|---|
| **DeepSeek-R1 14B** | `deepseek-r1:14b` | ~8.8 GB | Distilled from R1-671B. Strong reasoning + code; the realistic reasoning model that fits. |
| **DeepSeek-R1 7B** | `deepseek-r1:7b` | ~4.7 GB | Lighter; weaker on edge cases. |
| **Phi-4 14B** | `phi4:14b` | ~8.5 GB | Microsoft. Strong structured reasoning, surprisingly good code given general-purpose training. |
| QwQ 32B | `qwq:32b` | ~19 GB | **Doesn't fit.** Listed for completeness. |
| DeepSeek-R1 32B | `deepseek-r1:32b` | ~20 GB | **Doesn't fit.** |

### 4.3 Embeddings (already in use)

| Model | Ollama tag | File size | Notes |
|---|---|---|---|
| **nomic-embed-text** | `nomic-embed-text` | 0.3 GB | 768-d output. Already pulled. CFP pipeline embeddings. |

---

## 5. FPGA / RTL / Verilog / VHDL — Specialist Models

The honest state, from CodeV-R1 page + VerilogEval v2 + ResBench + HLS-Eval research:

| Model | Type | VerilogEval v2 pass@1 | RTLLM v1.1 pass@1 | Size | Where to get it |
|---|---|---|---|---|---|
| **CodeV-R1-RL-Qwen-7B** | Verilog specialist | **68.6%** | **72.9%** | 7B (Qwen2.5-Coder-Instruct-7B base, RLVR fine-tuned) | HuggingFace GGUF — **NOT on Ollama**; load via Ollama modelfile |
| CodeV-R1-Distill-Qwen-7B | Distillation phase | slightly lower than RL | — | 7B | HuggingFace |
| RTLCoder 6.7B | Verilog specialist | 61.2% | — | 6.7B (DeepSeek-Coder fine-tune) | HuggingFace — **NOT on Ollama** |
| VeriCoder | Verilog specialist | 55.7% | — | varies | Research code |
| OriGen | Verilog specialist | 54.4% (Human) / 74.1% (Machine) | — | varies | Research code |
| GPT-4o (cloud, reference) | General frontier | ~63% (spec-to-RTL) | — | — | Cloud only |

**Two important findings from 2025 papers:**

1. **General frontier models are catching up.** Recent results (Revisiting VerilogEval, ACM TODAES 2025; ResBench 2025) show GPT-4.1, Claude Sonnet 4, and Qwen3 family without fine-tuning can match or beat domain-specialist pipelines on pass@1. The specialist edge has narrowed to single-digit points and exists mainly on harder benchmarks (IC-RTL, RTLLM v2).

2. **No good open small specialist for HLS exists yet.** HLS-Eval (arXiv 2504.12268, April 2025) is the first complete HLS benchmark, and it found that for HLS C++ code generation and pragma-based optimization, **general code models perform comparably to or better than the few HLS-tuned attempts**. Use Devstral / DeepSeek-Coder-V2 + good prompting.

### 5.1 Recommended FPGA/HLS workflow on this machine

```
codev-r1-rl-qwen-7b   (HF GGUF, ~5 GB)   ← Verilog/RTL generation, debugging
devstral-small-2:24b   (15 GB)            ← HLS C++, complex multi-file refactor
codestral:22b          (12 GB)            ← Pragma-heavy autocomplete, broad lang FIM
deepseek-r1:14b        (8.8 GB)           ← Reasoning over RTL synthesis errors
```

---

## 6. CUDA / GPU-Kernel Programming

**Honest finding: no specialized small open model exists yet** for CUDA kernel generation/optimization that fits 16 GB.

- **KernelBench** (2024) and **CUDABench** (2026) exist as evaluation harnesses, but no fine-tuned model of useful size has been released.
- General code models are the only option. Empirical ranking on KernelBench-style tasks (per recent literature):
  1. DeepSeek-Coder-V2 Lite 16B (best single-model CUDA score in this size class)
  2. Devstral Small 2 24B (good agentic — can iterate on kernels)
  3. Qwen2.5-Coder 14B (strong fundamentals)

Same recommendation for **TPU / XLA / JAX**: no specialist model exists. DeepSeek-Coder-V2 has the most JAX/XLA in training data of the open small set.

---

## 7. Conflicts and Unknowns Surfaced

These are flagged for re-checking before publishing benchmark numbers in any document:

| # | Issue | Sources in disagreement |
|---|---|---|
| 1 | Qwen2.5-Coder 7B HumanEval: 61.6% vs 88.4% | Paper score is for base; blog score is for instruct variant. Always specify which. |
| 2 | Qwen2.5-Coder 14B HumanEval: 64.0% vs ~89% | Same base/instruct issue. |
| 3 | Devstral Small 2 SWE-bench score | Vendor claims agent mode ≥50%; no third-party SWE-bench Verified leaderboard entry seen. Treat as approximate. |
| 4 | Qwen3-Coder 30B Q4_K_M file size | 16–17 GB estimate; not measured. May not load with default context on 16 GB GPU. |
| 5 | Sub-agent's earlier claim about "Qwen3-Coder-Next 80B MoE = 8–10 GB Q4_K_M" | This is wrong: even a 3B-active MoE still has 80B parameters that all need to live in VRAM at full unload. Disregard. |
| 6 | Kimi K2.5 / GLM-5 SWE-bench scores | Authoritative but only relevant as ceiling reference — both are too large for our hardware. |

**Lesson for next research run:** Always fetch the model card on HuggingFace for the *specific* tag we plan to pull (e.g. `deepseek-coder-v2:16b-lite-instruct-q4_K_M`) and read its eval section directly, rather than relying on aggregator sites.

---

## 8. Final Recommendation Set for This Machine

In install priority order. Total ~45 GB on D:\wsl\ollama (out of 248 GB free).

```
# Already pulled
nomic-embed-text         0.3 GB   embeddings
qwen3.5:4b               3.4 GB   CFP Tier 1 triage

# Pull next — these are the no-regrets picks
qwen2.5-coder:14b       10 GB    primary local code model (verify instruct tag)
deepseek-r1:14b          8.8 GB  reasoning + dedup confirmation
deepseek-coder-v2:16b    9 GB    long-context (128K) + MoE efficiency
codestral:22b           12 GB    FIM autocomplete + broad lang
devstral-small-2:24b    15 GB    agentic coding (tight fit; pull last, test fit)

# Manual install via Ollama Modelfile
codev-r1-rl-qwen-7b     ~5 GB    Verilog/RTL specialist (HuggingFace GGUF)

# Remove: superseded
qwen3:4b                 2.5 GB   (qwen3.5:4b is the current generation)
```

**Why each one:**

- `qwen2.5-coder:14b` — best documented general code model that fits with margin. Long-running reliability is well established by Sept 2024 release + 14 months of community use.
- `deepseek-r1:14b` — only reasoning model that fits with margin; needed for Tier 4 of the CFP pipeline (dedup confirmation) per `arch.md §1 Q5`.
- `deepseek-coder-v2:16b` — 128K context and MoE efficiency make it the best choice when working with long files / repos.
- `codestral:22b` — uniquely strong at fill-in-the-middle for autocomplete; covers VHDL, TCL, HLS pragmas where pure code models hallucinate.
- `devstral-small-2:24b` — best modern coding model that fits at all on 16 GB. Tight; verify with realistic context before committing.
- `codev-r1-rl-qwen-7b` — only Verilog model with credible benchmark gains over general models. Manual install acceptable given specialist value.

---

## 9. References

### Primary leaderboards / aggregators
- [Best LLMs for Coding 2026 — Onyx AI](https://onyx.app/insights/best-llms-for-coding-2026)
- [BenchLM.ai open-weight leaderboard](https://benchlm.ai/blog/posts/best-open-source-llm)
- [Vellum LLM Leaderboard 2026](https://www.vellum.ai/llm-leaderboard)
- [Best Ollama Models for Coding 2026 — aimadetools](https://www.aimadetools.com/blog/best-ollama-models-coding-2026/)

### Ollama registry
- [Ollama coder search](https://ollama.com/search?q=coder)
- [Ollama library (sorted by newest)](https://ollama.com/library?sort=newest)
- [devstral-small-2:24b](https://ollama.com/library/devstral-small-2)
- [qwen2.5-coder](https://ollama.com/library/qwen2.5-coder)
- [deepseek-coder](https://ollama.com/library/deepseek-coder)

### FPGA / RTL / HLS specialist research
- [CodeV-R1 project page](https://iprc-dip.github.io/CodeV-R1/)
- [VerilogEval (arXiv 2309.07544)](https://arxiv.org/abs/2309.07544)
- [Revisiting VerilogEval — ACM TODAES 2025](https://dl.acm.org/doi/10.1145/3718088)
- [RTLCoder GitHub (HKUST-Zhiyao)](https://github.com/hkust-zhiyao/RTL-Coder)
- [HLS-Eval (arXiv 2504.12268)](https://arxiv.org/abs/2504.12268)
- [HLStrans (arXiv 2507.04315)](https://arxiv.org/html/2507.04315v1)
- [ResBench (Imperial College, 2025)](https://www.doc.ic.ac.uk/~cg1710/pub/2025/heart25tz.pdf)
- [ProtocolLLM (arXiv 2506.07945)](https://www.arxiv.org/pdf/2506.07945)
- [Survey: LLMs for RTL Code Generation (Preprints, Sept 2025)](https://www.preprints.org/manuscript/202509.1681)

### Specific large models (for "doesn't fit" reference)
- [Kimi K2.5 / K2.6 — Unsloth docs](https://unsloth.ai/docs/models/kimi-k2.5)
- [Kimi K2.6 blog](https://www.kimi.com/blog/kimi-k2-6)
- [GitHub: ollama/ollama (model index)](https://github.com/ollama/ollama)

### CUDA / kernel benchmarks
- [KernelBench](https://github.com/ScalingIntelligence/KernelBench)
- [CUDABench (arXiv 2603.02236)](https://arxiv.org/html/2603.02236)

---

## 10. When to Re-Run This Research

Trigger a refresh when any of these is true:
- More than 3 months since `Last updated` at top of this file
- A new flagship open-weight model is announced (Qwen 4, DeepSeek V5, Llama 5, Kimi K3, GLM-6, etc.)
- Devstral Small 3, Qwen3-Coder updates, or new Verilog specialists appear on Ollama
- Hardware changes (new GPU, more VRAM)
- A new benchmark dataset becomes the de-facto standard (e.g. successor to LiveCodeBench)
