# python_scripts/discover_tessie.py


def repr(val) -> str:
    if val is None:
        return str(val)

    typeStr = "unknown"
    try:
        _ = val[0]
        typeStr = "str"
    except:
        try:
            _ = val // 1
            typeStr = "int"
        except:
            try:
                _ = val / 1.0
                typeStr = "float"
            except:
                pass
    return f"{val} ({typeStr})"


all_states = hass.states.all()
tessie_entities = {}

for state in all_states:
    # Look for tessie in the entity_id or attributes
    if "nittany" in state.entity_id or "electra" in state.entity_id:
        entity_type = state.entity_id.split(".")[0]
        if entity_type not in tessie_entities:
            tessie_entities[entity_type] = []
        tessie_entities[entity_type].append(state.entity_id)

# Create a readable output
output_str = "Tessie Entities Found:\n\n"
for entity_type, entities in sorted(tessie_entities.items()):
    output_str += f"{entity_type.upper()}:\n"
    for entity in sorted(entities):
        state = hass.states.get(entity)
        output_str += f" - {state.name} {entity} ({repr(state.state)})"
        attribs = state.attributes
        if attribs:
            attribs = attribs.copy()
            del attribs["friendly_name"]
            if attribs:
                attrib_strs = [f"{key}={repr(val)}" for key, val in attribs.items()]
                output_str += f"\tattributes: {', '.join(attrib_strs)}"
        output_str += "\n"
    output_str += "\n"

logger.info(output_str)

hass.services.call(
    "persistent_notification",
    "create",
    {"title": "Tessie Entity Discovery", "message": output_str},
)
