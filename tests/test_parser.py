from app.models import Timeline,Source,Segment,Word
from app.engine import History
from app.parser import execute_instruction

def history():
 t=Timeline(timeline_id='t',source=Source(media_id='m',sha256='0'*64,duration_s=100),transcript=[Word(id=f'w{i}',start_s=i*10,end_s=i*10+1,text=x) for i,x in enumerate(['welcome','to','the','channel','price','is','high','price','changes'])],segments=[Segment(id='s',source_start_s=0,source_end_s=100)])
 return History(t)
def test_instruction_cut_applied():
 h=history();r=execute_instruction(h,'Cut the first 10 seconds');assert r['timeline'].segments[0].source_start_s==10
def test_unsupported_pricing_removal_is_rejected():
 r=execute_instruction(history(),'Remove the part about pricing');assert r['rejected']
def test_unknown_rejected():
 assert execute_instruction(history(),'make it cinematic')['rejected']
def test_speed_current_time():
 r=execute_instruction(history(),'Speed up from 1:00 to 1:10 by 2x');assert r['op']['speed']==2
def test_caption_occurrence_selection():
 h=history();r=execute_instruction(h,'Add caption "Cost" when I say "price"');assert r['clarification']
 resolved=execute_instruction(h,'Add caption "Cost" when I say "price"',2)
 assert resolved['op']['anchor_word_ids']==['w7']
def test_frontend_curly_quote_caption_suggestion():
 r=execute_instruction(history(),'Add caption “Welcome!” when I say “welcome”')
 assert r['op']['text']=='Welcome!'

def test_move_clip_reorders_and_can_undo():
 h=history()
 from app.models import Segment
 h.current.segments=[Segment(id='one',source_start_s=0,source_end_s=10),Segment(id='two',source_start_s=20,source_end_s=30)]
 result=execute_instruction(h,'move clip 1 to position 2')
 assert result['op']['type']=='reorder'
 assert [segment.id for segment in h.current.segments]==['two','one']
 h.undo()
 assert [segment.id for segment in h.current.segments]==['one','two']

def test_remove_filler_words_uses_transcript_timing():
 h=history()
 h.current.transcript=[Word(id='filler',start_s=20,end_s=21,text='um')]
 result=execute_instruction(h,'remove filler words')
 assert result['op']['type']=='remove_fillers'
 assert all(not (segment.source_start_s<21 and segment.source_end_s>20) for segment in h.current.segments)

def test_bleep_instruction_adds_renderable_source_interval():
 h=history()
 result=execute_instruction(h,'bleep from 4 seconds to 5 seconds')
 assert result['op']['type']=='bleep'
 assert h.current.bleeps[0].source_start_s==4
 assert h.current.bleeps[0].source_end_s==5
