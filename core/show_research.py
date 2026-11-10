"""Bounded automatic reference drafts. No user footage is uploaded or auto-approved."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path

# These are links, not redistributed third-party footage. Clip sources retain attribution.
TEXTURE_SOURCES=[
    {'name':'Electricity · Green Screen Garf','texture':'electricity','url':'https://www.youtube.com/watch?v=JBXG0HfrXlA','start':20.,'end':26.,'source':'https://www.youtube.com/watch?v=JBXG0HfrXlA'},
    {'name':'Rain · green-screen stock footage','texture':'rain','url':'https://www.youtube.com/watch?v=1LaoMlzr5wg','start':0.,'end':6.,'source':'https://www.youtube.com/watch?v=1LaoMlzr5wg'},
]


def search_videos(query):
    from yt_dlp import YoutubeDL
    # Flat metadata only; no cookies, private files or downloads are sent to search.
    with YoutubeDL({'quiet':True,'no_warnings':True,'extract_flat':True,'skip_download':True,'socket_timeout':10,'retries':0}) as ydl:
        doc=ydl.extract_info('ytsearch3:'+str(query)[:160],download=False)
    results=[]
    for entry in (doc or {}).get('entries') or []:
        key=entry.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',key):continue
        results.append({'url':'https://www.youtube.com/watch?v='+key,'title':str(entry.get('title') or query)[:180],
                        'channel':str(entry.get('channel') or entry.get('uploader') or '')[:120]})
    return results


def prepare(project,items,*,ffmpeg,progress=None,cancel=None):
    """Prepare up to three director-selected drafts; failures remain explicit proposals.

    Render/export never calls this function. The chosen clip is a reviewable draft,
    not a claim that a search result is the best joke or that it may be published.
    """
    from core import show_clips,show_references,show_proposals,show_video_assets
    reports=[];resources=[];count=0
    for item in items:
        p=item['params']
        if not p.get('auto_research') or count>=3:continue
        if cancel:cancel()
        count+=1
        if progress:progress(f"Finding a reference for {p.get('query') or p.get('subject') or 'this moment'}")
        try:
            if item['kind']=='clip':
                candidates=search_videos(p['query']);p['reference_candidates']=candidates
                if not candidates:raise ValueError('No matching public video reference was found')
                chosen=candidates[0];start=0.;length=min(6.,max(.5,float(item['duration'])))
                source=p.get('texture_footage')
                curated=next((r for r in TEXTURE_SOURCES if r['texture']==source),None)
                if curated:chosen={**chosen,**curated};start=curated['start'];length=min(length,curated['end']-start)
                reference={'id':'research-'+hashlib.sha256((chosen['url']+str(start)+str(length)).encode()).hexdigest()[:20],
                           'url':chosen['url'],'start':start,'end':start+length}
                clip=show_clips.download(project,reference,ffmpeg)
                p.update(clip=str(clip),clip_start=0.,clip_length=length,url=chosen['url'],source_start=start,
                         source_title=chosen.get('title') or chosen.get('name'),clip_audio=not bool(source),audio_mode='mute' if source else 'own')
                if source:p.update(matte='green-light' if show_video_assets.detect_green(clip) else 'black',person_scale=.4,person_offset=[0.,-.25])
                resources.append({'path':str(clip),'kind':'video','duration':length,'speech':[],'intervals':[],'origin':'Automatic reference draft · '+chosen['url']})
                show_video_assets.remember(project,item)
            else:
                found=show_references.search(p.get('query') or p['subject'])
                if not found:raise ValueError('No reference picture was found')
                result=show_references.import_photo(project,found[0]['title'])
                # import_photo returns its photo record and source provenance.
                path=result.get('path') or result.get('photo')
                if not path:raise ValueError('The picture import returned no reusable file')
                p.update(picture=path,image_engine='Wikimedia Commons · automatic reference draft',draft=False)
            p.pop('research_error',None);item['needs']=show_proposals.check(item)
            reports.append({'id':item['id'],'status':'draft','query':p.get('query') or p.get('subject')})
        except Exception as exc:
            # Cancellation must leave analysis; network/provider failures keep an editable card.
            if cancel:cancel()
            p['research_error']=str(exc)[:700];item['needs']=show_proposals.check(item)
            reports.append({'id':item['id'],'status':'unavailable','reason':str(exc)[:700]})
    for item in items:
        if item['params'].get('auto_research'):
            item['needs']=show_proposals.check(item)
            if item['needs'] and not item['params'].get('research_error'):item['params']['research_error']='Automatic research is limited to three references. Choose this one manually.'
    return resources,reports
