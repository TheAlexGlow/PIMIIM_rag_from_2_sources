"""Deterministic teaching calculators exposed by the local MCP servers."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt

from .catalog import CATALOG


class Reading(BaseModel):
    value: float | int | None = None
    unit: str = ""
    interpretation: str = ""
    severity: Literal["neutral", "green", "yellow", "red"] = "neutral"
    components: dict[str, float | int | str] = Field(default_factory=dict)
    guidance: str = ""
    source: str
    warning: str | None = None


def bounds(title: str, unit: str, lower: float, upper: float, step: float = 1, *, exclusive=False):
    # Custom JSON-schema keywords keep plausible-range violations inside the tool,
    # where it can return a domain-specific warning with no derived value.
    return Field(title=title, description=unit, json_schema_extra={
        "x-min": lower, "x-max": upper, "x-step": step, "x-exclusive-min": exclusive,
    })


Weight = Annotated[StrictFloat, bounds("Масса", "кг", 20, 500, 0.1)]
Height = Annotated[StrictFloat, bounds("Рост", "см", 80, 250, 0.1)]
Systolic = Annotated[StrictFloat, bounds("Систолическое давление", "мм рт. ст.", 30, 300)]
Diastolic = Annotated[StrictFloat, bounds("Диастолическое давление", "мм рт. ст.", 10, 200)]
Age = Annotated[StrictInt, bounds("Возраст", "лет", 18, 120)]
Creatinine = Annotated[StrictFloat, bounds("Креатинин сыворотки", "мг/дл", 0, 40, 0.01, exclusive=True)]
Eyes = Literal["spontaneous", "sound", "pressure", "none"]
Verbal = Literal["oriented", "confused", "words", "sounds", "none"]
Motor = Literal["obeys", "localizes", "normal_flexion", "abnormal_flexion", "extension", "none"]


def range_warning(value: float, label: str, lower: float, upper: float, unit: str, *, exclusive=False) -> str | None:
    if math.isfinite(value) and lower <= value <= upper and (not exclusive or value > lower):
        return None
    interval = f"{'(' if exclusive else '['}{lower:g}; {upper:g}]"
    return f"{label} вне допустимого диапазона этой формы {interval} {unit}. Проверьте значение и единицы."


def bmi(weight_kg: Weight, height_cm: Height) -> Reading:
    """Индекс массы тела · BMI · Антропометрия."""
    source = CATALOG["bmi"].source
    warning = range_warning(weight_kg, "Масса", 20, 500, "кг") or range_warning(height_cm, "Рост", 80, 250, "см")
    if warning:
        return Reading(source=source, warning=warning)
    meters = height_cm / 100
    value = weight_kg / meters**2
    if value < 18.5:
        category, severity = "Недостаточная масса", "yellow"
    elif value < 25:
        category, severity = "Диапазон здоровой массы", "green"
    elif value < 30:
        category, severity = "Избыточная масса", "yellow"
    else:
        category, severity = "Диапазон ожирения", "red"
    return Reading(value=value, unit="кг/м²", interpretation=category, severity=severity,
                   components={"Масса, кг": weight_kg, "Рост, м": meters},
                   guidance="Сопоставьте скрининговую категорию с составом тела и клиническим контекстом.", source=source)


def body_surface_area(weight_kg: Weight, height_cm: Height) -> Reading:
    """Площадь поверхности тела · Mosteller BSA · Антропометрия."""
    source = CATALOG["body_surface_area"].source
    warning = range_warning(weight_kg, "Масса", 20, 500, "кг") or range_warning(height_cm, "Рост", 80, 250, "см")
    if warning:
        return Reading(source=source, warning=warning)
    return Reading(value=math.sqrt(weight_kg * height_cm / 3600), unit="м²", interpretation="Оценка по Мостеллеру",
                   components={"Масса, кг": weight_kg, "Рост, см": height_cm, "Делитель формулы": 3600},
                   guidance="Интерпретация площади тела зависит от задачи, для которой она рассчитывается.", source=source)


def glasgow_coma_scale(
    eyes: Annotated[Eyes, Field(title="Открывание глаз")],
    verbal: Annotated[Verbal, Field(title="Речевой ответ")],
    motor: Annotated[Motor, Field(title="Моторный ответ")],
    components_testable: Annotated[StrictBool, Field(title="Все три ответа удалось оценить")],
) -> Reading:
    """Шкала комы Глазго · GCS · Реанимация."""
    source = CATALOG["glasgow_coma_scale"].source
    if not components_testable:
        return Reading(source=source, warning="Отметьте недоступные компоненты как NT и укажите причину. Итоговая сумма не рассчитана.")
    e = {"spontaneous": 4, "sound": 3, "pressure": 2, "none": 1}[eyes]
    v = {"oriented": 5, "confused": 4, "words": 3, "sounds": 2, "none": 1}[verbal]
    m = {"obeys": 6, "localizes": 5, "normal_flexion": 4, "abnormal_flexion": 3, "extension": 2, "none": 1}[motor]
    total = e + v + m
    if total <= 8:
        description, severity = "Диапазон тяжёлой ЧМТ", "red"
    elif total <= 12:
        description, severity = "Диапазон ЧМТ средней тяжести", "yellow"
    else:
        description, severity = "Диапазон лёгкой ЧМТ", "green"
    return Reading(value=total, unit="баллов", interpretation=description, severity=severity,
                   components={"E · Глаза": e, "V · Речь": v, "M · Моторика": m},
                   guidance="Стратификация приведена для контекста ЧМТ. Документируйте каждый компонент и условия оценки.", source=source)


def mean_arterial_pressure(systolic: Systolic, diastolic: Diastolic) -> Reading:
    """Среднее артериальное давление · MAP · Кардиология."""
    source = CATALOG["mean_arterial_pressure"].source
    warning = range_warning(systolic, "САД", 30, 300, "мм рт. ст.") or range_warning(diastolic, "ДАД", 10, 200, "мм рт. ст.")
    if warning or systolic <= diastolic:
        return Reading(source=source, warning=warning or "Систолическое давление должно быть выше диастолического.")
    pulse_pressure = systolic - diastolic
    return Reading(value=diastolic + pulse_pressure / 3, unit="мм рт. ст.", interpretation="Приближённая оценка MAP",
                   components={"САД": systolic, "ДАД": diastolic, "Пульсовое давление": pulse_pressure},
                   guidance="Сопоставьте результат с методом измерения и гемодинамическим контекстом.", source=source)


def egfr_ckd_epi(
    age: Age, sex: Annotated[Literal["female", "male"], Field(title="Пол для формулы")], creatinine_mg_dl: Creatinine,
) -> Reading:
    """Расчётная СКФ · eGFR CKD-EPI 2021 · Нефрология."""
    source = CATALOG["egfr_ckd_epi"].source
    warning = range_warning(age, "Возраст", 18, 120, "лет") or range_warning(creatinine_mg_dl, "Креатинин", 0, 40, "мг/дл", exclusive=True)
    if warning:
        return Reading(source=source, warning=warning)
    kappa, alpha, sex_factor = (0.7, -0.241, 1.012) if sex == "female" else (0.9, -0.302, 1.0)
    ratio = creatinine_mg_dl / kappa
    if ratio == 0 or not math.isfinite(ratio):
        return Reading(source=source, warning="Креатинин не позволяет численно оценить СКФ. Проверьте единицы.")
    value = 142 * min(ratio, 1)**alpha * max(ratio, 1)**(-1.2) * 0.9938**age * sex_factor
    return Reading(value=value, unit="мл/мин/1,73 м²", interpretation="Оценка CKD-EPI 2021 по креатинину",
                   components={"Возраст, лет": age, "Креатинин, мг/дл": creatinine_mg_dl, "κ": kappa, "α": alpha, "Множитель пола": sex_factor},
                   guidance="Для оценки хронической болезни почек учитывайте динамику СКФ и маркеры повреждения, включая альбуминурию.", source=source)
