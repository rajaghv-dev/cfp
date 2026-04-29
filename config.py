from pathlib import Path
import os

ROOT         = Path(__file__).parent
DATA_DIR     = ROOT / "data"
REPORTS_DIR  = ROOT / "reports"
ONTOLOGY_DIR = ROOT / "ontology"

PG_DSN = os.getenv("PG_DSN", "postgresql://cfp:cfp@localhost:5432/cfp")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

CFP_MACHINE = os.getenv("CFP_MACHINE", "gpu_mid")
# valid: dgx | gpu_large | gpu_mid | gpu_small | cpu_only

# Per-profile model roster. Pinned quant tags per arch.md §1 Q14.
# mistral-nemo:12b on gpu_mid+ for long-context HTML routing.
PROFILE_MODELS: dict[str, list[str]] = {
    "dgx":       ["qwen3:4b-q8_0",   "qwen3:14b-q8_0",   "qwen3:32b-q8_0",
                  "deepseek-r1:32b",  "deepseek-r1:70b",
                  "mistral-nemo:12b", "nomic-embed-text"],
    "gpu_large": ["qwen3:4b-q4_K_M", "qwen3:14b-q4_K_M", "qwen3:32b-q4_K_M",
                  "deepseek-r1:32b",  "mistral-nemo:12b", "nomic-embed-text"],
    "gpu_mid":   ["qwen3:4b-q4_K_M", "qwen3:14b-q4_K_M",
                  "mistral-nemo:12b", "nomic-embed-text"],
    "gpu_small": ["qwen3:4b-q4_K_M", "nomic-embed-text"],
    "cpu_only":  ["qwen3:4b-q4_K_M", "nomic-embed-text"],
}

TIER_THRESHOLD = {1: 0.85, 2: 0.85, 3: 0.80}

LONG_CONTEXT_TOKENS = 32_000

AGE_GRAPH = "cfp_graph"

EMBED_DIM         = 768
DEDUP_COSINE      = 0.92
DEDUP_AUTO_MERGE  = 0.97
DEDUP_TOP_K       = 5

USER_AGENT            = os.getenv("USER_AGENT",
                                  "cfp-scraper/1.0 (+contact@example.com)")
HUMAN_DELAY_MEAN      = 8.0
HUMAN_DELAY_STD       = 2.5
HUMAN_DELAY_MIN       = 5.0
HUMAN_DELAY_MAX       = 15.0
HUMAN_DELAY_LONG_PROB = 0.10

MAX_RETRIES        = 5
RETRY_BACKOFF_BASE = 2.0
RETRY_BACKOFF_CAP  = 600
DEAD_LETTER_KEY    = "cfp:dead"

# LLM JSON failure recovery (arch.md §1 Q12)
JSON_REPAIR_ENABLED   = True
JSON_RETRY_SAME_TIER  = 1
PARSE_FAIL_THRESHOLD  = 0.01

GCS_BUCKET    = os.getenv("GCS_BUCKET", "cfp-data")
GCS_PREFIX    = os.getenv("GCS_PREFIX", "prod")
RCLONE_REMOTE = os.getenv("RCLONE_REMOTE", "gcs")

PROMPTS_FILE  = ROOT / "prompts.md"
SEED_JSON     = DATA_DIR / "latest.json"
