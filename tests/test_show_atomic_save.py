"""A reader must never see the temporary empty state of a background draft save."""
import threading
from core import show_proposals,show_scenes


def test_readers_see_complete_proposals_and_scenes_during_background_writes(tmp_path):
    proposals=tmp_path/'proposals.json';scenes=tmp_path/'scenes.json'
    show_proposals.save(proposals,[]);show_scenes.save(scenes,{'scenes':[]})
    failures=[]
    def writer():
        try:
            for n in range(30):
                payload=[{'id':str(n),'payload':'x'*40000}]
                show_proposals.save(proposals,payload)
                show_scenes.save(scenes,{'scenes':payload})
        except BaseException as exc:failures.append(exc)
    thread=threading.Thread(target=writer);thread.start()
    while thread.is_alive():
        for path,reader,key in [(proposals,show_proposals.load,'proposals'),(scenes,show_scenes.load,'scenes')]:
            document=reader(path)
            assert isinstance(document[key],list)
            if document[key]:assert len(document[key][0]['payload'])==40000
    thread.join();assert not failures
