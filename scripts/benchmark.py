"""Mede o throughput do sistema: envia K imagens PNG e espera todas terminarem.

Uso (com o ambiente no ar):
    python scripts/benchmark.py --count 12
    docker compose up -d --scale worker=1  &&  python scripts/benchmark.py --count 12
    docker compose up -d --scale worker=4  &&  python scripts/benchmark.py --count 12

Compare o "tempo total" entre as execuções: é a evidência de escalabilidade
pedida no relatório.
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image


def make_png(i: int, size: int) -> bytes:
    """Imagem com ruído (não comprime quase nada) e um canal alfa."""
    noise = Image.effect_noise((size, size), 64).convert("L")
    img = Image.merge("RGBA", (noise, noise.rotate(90), noise.rotate(180),
                               Image.new("L", (size, size), 128 + i % 100)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080", help="URL do frontend ou da API")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--size", type=int, default=800, help="lado da imagem em pixels")
    ap.add_argument("--email", default="benchmark@exemplo.com")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    print(f"Gerando {args.count} PNGs de {args.size}x{args.size}...")
    images = [make_png(i, args.size) for i in range(args.count)]

    def submit(i: int):
        t0 = time.perf_counter()
        res = requests.post(
            f"{args.api}/api/jobs",
            files={"file": (f"bench-{i:03d}.png", images[i], "image/png")},
            data={"targetFormat": "jpg", "email": args.email},
            timeout=30,
        )
        res.raise_for_status()
        return res.json()["jobId"], (time.perf_counter() - t0) * 1000

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(submit, range(args.count)))
    submit_s = time.perf_counter() - start
    ids = [a[0] for a in accepted]
    print(f"{len(ids)} pedidos aceitos em {submit_s:.2f} s "
          f"(resposta da API: média {sum(a[1] for a in accepted) / len(accepted):.0f} ms)")

    pending = set(ids)
    final: dict[str, dict] = {}
    while pending and time.perf_counter() - start < args.timeout:
        for job_id in list(pending):
            job = requests.get(f"{args.api}/api/jobs/{job_id}", timeout=10).json()
            if job["status"] in ("CONCLUIDO", "ERRO"):
                final[job_id] = job
                pending.discard(job_id)
        print(f"\r  concluídos: {len(final)}/{len(ids)}", end="", flush=True)
        time.sleep(0.5)
    total = time.perf_counter() - start
    print()

    if pending:
        print(f"TEMPO ESGOTADO: {len(pending)} pedidos não terminaram")
        return 1

    done = [j for j in final.values() if j["status"] == "CONCLUIDO"]
    per_worker = Counter(j["workerId"] for j in done)
    avg_conv = sum(j["durationSeconds"] or 0 for j in done) / max(1, len(done))

    print("\n=== Resultado ===")
    print(f"Workers que participaram : {len(per_worker)}")
    print(f"Pedidos                  : {len(ids)} ({len(done)} concluídos, {len(ids) - len(done)} com erro)")
    print(f"Tempo total              : {total:.2f} s")
    print(f"Throughput               : {len(done) / total:.2f} conversões/s")
    print(f"Tempo médio por conversão: {avg_conv:.2f} s")
    print("Distribuição por worker  :")
    for worker, n in sorted(per_worker.items()):
        print(f"  {worker:<28} {n}")
    print("\nLinha para a tabela do relatório:")
    print(f"| {len(per_worker)} | {len(ids)} | {total:.1f} s | {len(done) / total:.2f}/s | {avg_conv:.2f} s |")
    return 0 if len(done) == len(ids) else 2


if __name__ == "__main__":
    sys.exit(main())
