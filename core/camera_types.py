"""User-assigned camera types; independent of manufacturer or file extension."""
from pathlib import Path

def kind(project,segment):
    settings=project.data.get('settings',{}).get('edit',{})
    path=str(segment.get('source_path') or segment.get('clip_path') or segment.get('path') or '')
    if segment.get('spherical_shot') or segment.get('projection') in ('equirect','raw_insv'):return '360'
    assigned=(settings.get('camera_types') or {}).get(path)
    if assigned in ('fixed','moving'):return assigned
    role=(settings.get('camera_roles') or {}).get(path) or segment.get('camera_role') or segment.get('role')
    if role=='fixed_rear':return 'fixed'
    if role=='handheld':return 'moving'
    name=Path(path).stem.lower()
    return 'fixed' if any(word in name for word in ('fixed','tripod','static')) else 'moving'

def apply(record,assignments):
    value=assignments.get(record['path'])
    if not value:return
    projection=('raw_insv' if Path(record['path']).suffix.lower()=='.insv' else 'equirect') if value=='360' else None
    record['projection']=projection;record.setdefault('probe',{})['projection']=projection
    if value!='360':record['probe'].pop('input_projection',None)
