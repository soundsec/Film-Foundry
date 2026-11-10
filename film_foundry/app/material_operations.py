"""Operation descriptors drawn from the families' actual callable signatures."""
import inspect
from film_foundry.core import peel_apart,integral_instant,sheet_common,material_defects


def operation_functions(module):
    return dict(module.OPERATION_FUNCTIONS)


def parameter_spec(module,operation):
    if operation=='expose':return {}
    if operation=='prepare':
        if not hasattr(module,'prepare'):raise ValueError('This material has no separate preparation operation')
        return {}
    functions=operation_functions(module)
    if operation not in functions:raise ValueError(f'Unsupported operation: {operation}')
    function=functions[operation]
    if operation=='handling':
        function=material_defects.handling_density if module in (peel_apart,integral_instant) else sheet_common.handling
    extras=[]
    if module is peel_apart and operation=='handling':extras.append(peel_apart.handling)
    if module is integral_instant and operation in ('spread','light_leak'):
        extras.append(getattr(peel_apart,operation))
    result={}
    for fn in (*extras,function):
        for key,param in inspect.signature(fn).parameters.items():
            if key in ('state','shape','seed') or param.kind in (param.VAR_KEYWORD,param.VAR_POSITIONAL):continue
            result[key]=param
    return result


def resolve_parameters(module,operation,parameters):
    if not isinstance(parameters,dict):raise ValueError('Operation parameters must be an object')
    spec=parameter_spec(module,operation)
    if set(parameters)-set(spec):raise ValueError(f'Unknown {operation} parameters: {sorted(set(parameters)-set(spec))}')
    result={}
    for key,param in spec.items():
        if key in parameters:result[key]=parameters[key]
        elif param.default is not inspect.Parameter.empty:result[key]=param.default
        else:raise ValueError(f'{operation} requires {key}')
    return result
