"""Generate the synthetic dataset.

    uv run python -m datagen.generate                      # template renderer, both splits
    uv run python -m datagen.generate --renderer model     # your own model via LiteLLM
    uv run python -m datagen.generate --dry-run            # print plans, write nothing

The model renderer reads LLM_MODEL, LLM_API_KEY and LLM_API_BASE from .env (see
.env.example) and stops cleanly when --max-cost is reached.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from copilot.evaluation.dataset import LABEL_FORMAT
from datagen.labels import build_labels
from datagen.messify import Messifier
from datagen.plan import TRAIT_NAMES, plan_split
from datagen.render_template import TemplateRenderer
from datagen.schema import RenderInfo
from datagen.validate import validate_pair
from datagen.world import DATAGEN, load_world

ROOT = DATAGEN.parent


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def make_renderer(kind: str, world, seed: int, max_cost: float | None):
    if kind == "template":
        return TemplateRenderer(world, seed)
    from datagen.render_model import ModelRenderer

    return ModelRenderer(world, seed, max_cost=max_cost)


def generate_split(
    world, mix, split: str, count: int, seed: int, renderer_kind: str, max_cost: float | None
):
    plans = plan_split(world, mix, split, count, seed)
    renderer = make_renderer(renderer_kind, world, seed + 1, max_cost)
    messifier = Messifier(world, seed + 2)
    threads, labels = [], []
    for plan in plans:
        rendered = renderer.render(plan)
        thread = messifier.build(plan, rendered)
        info = RenderInfo(
            renderer=renderer.name, prompt_version=renderer.prompt_version, model=renderer.model
        )
        label = build_labels(world, plan, thread, info)
        problems = validate_pair(thread, label)
        if problems:
            raise SystemExit(f"{plan.thread_id} failed validation: {problems}")
        threads.append(thread)
        labels.append(label)
    return threads, labels, renderer


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--renderer", choices=["template", "model"], default="template")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--count", type=int, help="Override the number of threads per split.")
    parser.add_argument("--out", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Stop the model renderer at this spend in USD (default SPEND_CAP_USD).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan summaries only.")
    args = parser.parse_args(argv)

    world = load_world()
    mix_path = DATAGEN / "scenarios" / "mix.yaml"
    mix = yaml.safe_load(mix_path.read_text())
    splits = ["dev", "test"] if args.split == "all" else [args.split]

    manifest_path = args.out / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"splits": {}}
    total_cost = 0.0
    for split in splits:
        cfg = mix["splits"][split]
        count = args.count or cfg["count"]
        if args.dry_run:
            for plan in plan_split(world, mix, split, count, cfg["seed"]):
                traits = [t for t in TRAIT_NAMES if getattr(plan.traits, t)]
                print(
                    f"{plan.thread_id} {plan.client_id:12} {plan.issue_id:22} "
                    f"{len(plan.messages)} msgs {','.join(traits)}"
                )
            continue
        try:
            threads, labels, renderer = generate_split(
                world, mix, split, count, cfg["seed"], args.renderer, args.max_cost
            )
        except RuntimeError as error:  # includes SpendCapReached
            print(f"{split}: stopped, nothing written for this split. {error}")
            return 1
        write_jsonl(args.out / split / "threads.jsonl", threads)
        write_jsonl(args.out / split / "labels.jsonl", labels)
        total_cost += renderer.cost_usd
        manifest["splits"][split] = {
            "count": len(threads),
            "seed": cfg["seed"],
            "messages": sum(len(t.messages) for t in threads),
            "clients": dict(sorted(Counter(lab.client_id for lab in labels).items())),
            "traits": {t: sum(getattr(lab.traits, t) for lab in labels) for t in TRAIT_NAMES},
            "priorities": dict(sorted(Counter(lab.fields.priority for lab in labels).items())),
            "renderer": renderer.name,
            "model": renderer.model,
            "prompt_version": renderer.prompt_version,
            "cost_usd": round(renderer.cost_usd, 4),
        }
        print(f"{split}: {len(threads)} threads written to {args.out / split}")

    if args.dry_run:
        return 0
    manifest["label_format"] = LABEL_FORMAT
    manifest["inputs"] = {
        str(p.relative_to(ROOT)): _sha(p)
        for p in sorted(
            [
                mix_path,
                *(DATAGEN / "world").glob("*.yaml"),
                *(DATAGEN / "clients").glob("*.yaml"),
                *(DATAGEN / "prompts").glob("*.md"),
            ]
        )
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    if total_cost:
        print(f"Model cost: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
