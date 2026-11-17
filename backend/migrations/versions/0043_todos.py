"""Personal task assignment, review workflow and private evidence images."""
from alembic import op
import sqlalchemy as s
revision='0043'
down_revision='0042'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('todos',s.Column('id',s.Integer(),primary_key=True),s.Column('title',s.String(200),nullable=False),s.Column('description',s.Text(),nullable=False),s.Column('instructions',s.Text(),nullable=False),s.Column('creator_id',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('assignee_id',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('deadline',s.DateTime(timezone=True),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('creator_seen',s.Integer(),nullable=False),s.Column('assignee_seen',s.Integer(),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),s.Column('updated_at',s.DateTime(timezone=True),nullable=False),s.Column('completed_at',s.DateTime(timezone=True)),s.CheckConstraint("status in ('pending','in_progress','review','returned','completed')",name='ck_todo_status'))
    op.create_index('ix_todos_creator_updated','todos',['creator_id','updated_at'])
    op.create_index('ix_todos_assignee_status','todos',['assignee_id','status'])
    op.create_table('todo_events',s.Column('id',s.Integer(),primary_key=True),s.Column('todo_id',s.Integer(),s.ForeignKey('todos.id',ondelete='CASCADE'),nullable=False),s.Column('actor_id',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('action',s.String(20),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('note',s.Text(),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
    op.create_index('ix_todo_events_todo_id','todo_events',['todo_id'])
    op.create_table('todo_images',s.Column('id',s.String(32),primary_key=True),s.Column('owner_id',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('todo_id',s.Integer(),s.ForeignKey('todos.id',ondelete='CASCADE')),s.Column('event_id',s.Integer(),s.ForeignKey('todo_events.id',ondelete='CASCADE')),s.Column('data',s.LargeBinary(),nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False))
    op.create_index('ix_todo_images_owner_id','todo_images',['owner_id'])
    op.create_index('ix_todo_images_todo_id','todo_images',['todo_id'])

def downgrade():
    op.drop_table('todo_images');op.drop_table('todo_events');op.drop_table('todos')
