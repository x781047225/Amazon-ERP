"""Task priorities, cancellation and participant-specific CC read receipts."""
from alembic import op
import sqlalchemy as s
revision='0045'
down_revision='0044'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('todos',s.Column('priority',s.Integer(),nullable=False,server_default='2'))
    op.add_column('todos',s.Column('cancelled_at',s.DateTime(timezone=True)))
    op.create_check_constraint('ck_todo_priority','todos','priority between 1 and 4')
    op.drop_constraint('ck_todo_status','todos',type_='check')
    op.create_check_constraint('ck_todo_status','todos',"status in ('pending','in_progress','review','returned','completed','cancelled')")
    op.create_table('todo_cc',s.Column('todo_id',s.Integer(),s.ForeignKey('todos.id',ondelete='CASCADE'),primary_key=True),s.Column('user_id',s.Integer(),s.ForeignKey('users.id'),primary_key=True),s.Column('seen_version',s.Integer(),nullable=False,server_default='0'))
    op.create_index('ix_todo_cc_user_id','todo_cc',['user_id'])

def downgrade():
    if op.get_bind().scalar(s.text("select count(*) from todos where status='cancelled'")):
        raise RuntimeError('存在已撤销待办，不能降级丢失状态')
    op.drop_table('todo_cc')
    op.drop_constraint('ck_todo_status','todos',type_='check')
    op.create_check_constraint('ck_todo_status','todos',"status in ('pending','in_progress','review','returned','completed')")
    op.drop_constraint('ck_todo_priority','todos',type_='check')
    op.drop_column('todos','cancelled_at')
    op.drop_column('todos','priority')
