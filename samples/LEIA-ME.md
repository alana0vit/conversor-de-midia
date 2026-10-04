# Arquivos de teste

| Arquivo | O que acontece |
|---|---|
| `transparente.png` | PNG com transparência → JPG com fundo branco |
| `foto-celular.jpg` | JPG com rotação no EXIF → PNG já na orientação correta (em pé) |
| `corrompido.png` | assinatura PNG válida, dados cortados: passa na API, falha no worker e vai para a **DLQ** |
| `gif-disfarcado.png` | GIF renomeado para `.png`: a API recusa na hora (415), não chega à fila |
