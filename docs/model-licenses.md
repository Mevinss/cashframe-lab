# Модели и лицензии

Проверено 17 октября 2026 года. Выбран только стандартный **DINOv2 ViT-S/14**, не медицинские XRAY/Cell варианты и не расширенные модели с отдельными ограничениями.

- Официальный источник: https://github.com/facebookresearch/dinov2
- Фиксированная ревизия кода: `7764ea0f912e53c92e82eb78a2a1631e92725fc8`.
- Официальный README отдельно распространяет Apache 2.0 на код и веса DINOv2: https://github.com/facebookresearch/dinov2/tree/7764ea0f912e53c92e82eb78a2a1631e92725fc8#license
- Текст лицензии: https://github.com/facebookresearch/dinov2/blob/7764ea0f912e53c92e82eb78a2a1631e92725fc8/LICENSE
- Стандартные веса: https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_pretrain.pth

Apache 2.0 допускает коммерческое использование с соблюдением условий лицензии и уведомлений. Веса загружаются отдельной подготовительной командой; при обработке видео загрузки запрещены. В репозиторий веса не входят. Проверка SHA-256 весов фиксируется при подготовке и проверяется локально при запуске.

Установленные ImageNet-веса torchvision не выбраны: BSD-лицензия кода не снимает отдельные условия для pretrained models, о чём предупреждает официальный README: https://github.com/pytorch/vision/tree/v0.21.0#pre-trained-model-license . Мы не заявляем, что коммерческие условия этих весов проверены.
