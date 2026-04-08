from pydantic_ai import Agent
from pydantic import BaseModel, Field
import os
from dotenv import load_dotenv

load_dotenv()

class MetricsParams(BaseModel):
    is_valid: bool = Field(description="True if no warnings")
    extracted_length: float = Field(description="The path length extracted")

agent = Agent(
    model='gemini-1.5-flash',
    system_prompt='Analyze engineering extraction text and strictly return the fields.',
    result_type=MetricsParams
)

def run_agent():
    print('Testing agent...')
    print(agent.run_sync('Text: pipeline produced 50.5m length, all solid.').data)

if __name__ == '__main__':
    run_agent()
