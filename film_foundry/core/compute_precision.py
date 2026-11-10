"""Formation arithmetic budgets, independent of storage and observation."""


def contact_precision(processing):
    policy=processing.contact_precision
    if policy not in ('auto','float64'):raise ValueError('processing.contact_precision must be auto or float64')
    return 'float64' if policy=='float64' or processing.quality_mode in ('high','full','native') else 'float32'
