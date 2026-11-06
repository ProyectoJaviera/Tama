# Notas del banco del modelo local

- 2026-11-02 21:04: Renzo cerró los programas abiertos. RAM: 31,9 GB en total, 10,7 GB usados con
  qwen3:4b-instruct-2507 cargado (3,2 GB), 21,2 GB libres. Antes de cerrarlos: 19,5 GB usados.
- Límite del banco: si la RAM usada pasa de 28 GB se corta la eval. Si un MoE de ~19 GB no cabe, se
  repite con `TAMA_NUM_CTX=4096` antes de descartarlo.
