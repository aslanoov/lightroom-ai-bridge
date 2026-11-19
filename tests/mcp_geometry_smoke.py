"""Opt-in real stdio MCP read-only geometry smoke test (no photo writes)."""
import asyncio
import json
from pathlib import Path
import sys
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    uuid,out=sys.argv[1:]
    transport=StdioTransport(command=sys.executable,args=['-m','lightroom_bridge.mcp_server'])
    async with Client(transport) as client:
        names={t.name for t in await client.list_tools()}
        expected={'geometry_'+x for x in ('capture','plan','apply','audit','transfer','suggest')}
        assert expected<=names
        result=await client.call_tool('geometry_capture',{'uuid':uuid,'out_dir':out,'size':1600})
        payload=json.loads(result.content[0].text)
        assert payload['ok'],payload
        print(json.dumps({'registered_tools':sorted(expected),'capture':payload['result']}))
        Path(out,'mcp-smoke.json').write_text(json.dumps({'registered_tools':sorted(expected),'capture':payload['result']},indent=2)+'\n')

if __name__=='__main__':
    asyncio.run(main())
