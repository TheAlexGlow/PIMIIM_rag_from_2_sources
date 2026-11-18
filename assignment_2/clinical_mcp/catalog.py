"""Local presentation metadata and explicit synthetic examples."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Calculator:
    title: str
    category: str
    description: str
    source: str
    example: dict


CATALOG = {
    "bmi": Calculator(
        "Индекс массы тела · BMI", "Антропометрия", "Скрининговые категории ИМТ для взрослых от 20 лет.",
        "https://www.cdc.gov/bmi/adult-calculator/bmi-categories.html", {"weight_kg": 72, "height_cm": 175},
    ),
    "body_surface_area": Calculator(
        "Площадь поверхности тела · BSA", "Антропометрия", "Оценка площади поверхности тела по Мостеллеру.",
        "https://pubmed.ncbi.nlm.nih.gov/3657876/", {"weight_kg": 72, "height_cm": 175},
    ),
    "glasgow_coma_scale": Calculator(
        "Шкала комы Глазго · GCS", "Реанимация", "Сумма глазного, речевого и моторного ответов.",
        "https://pubmed.ncbi.nlm.nih.gov/4136544/",
        {"eyes": "sound", "verbal": "confused", "motor": "obeys", "components_testable": True},
    ),
    "mean_arterial_pressure": Calculator(
        "Среднее артериальное давление · MAP", "Кардиология", "Приближённое среднее давление из САД и ДАД.",
        "https://www.ncbi.nlm.nih.gov/books/NBK538226/", {"systolic": 120, "diastolic": 80},
    ),
    "egfr_ckd_epi": Calculator(
        "Расчётная СКФ · eGFR CKD-EPI 2021", "Нефрология", "Креатининовая формула CKD-EPI 2021 для взрослых.",
        "https://doi.org/10.1056/NEJMoa2102953", {"age": 55, "sex": "female", "creatinine_mg_dl": 0.9},
    ),
}

ENUM_LABELS = {
    "eyes": {"spontaneous": "4 · Спонтанно", "sound": "3 · На звук", "pressure": "2 · На давление", "none": "1 · Нет"},
    "verbal": {"oriented": "5 · Ориентирован", "confused": "4 · Спутанная речь", "words": "3 · Слова", "sounds": "2 · Звуки", "none": "1 · Нет"},
    "motor": {"obeys": "6 · Выполняет команды", "localizes": "5 · Локализует стимул", "normal_flexion": "4 · Нормальное сгибание", "abnormal_flexion": "3 · Патологическое сгибание", "extension": "2 · Разгибание", "none": "1 · Нет"},
    "sex": {"female": "Женский", "male": "Мужской"},
}
