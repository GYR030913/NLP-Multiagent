"""Bounded public GETs with TLS verification and DNS pinning on every hop."""
import http.client
import io
import ipaddress
import shutil
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urljoin,urlsplit


def get(url,max_bytes):
    from .tools import public_url,CheckedRedirect,PublicHTTPSHandler
    public_url(url)
    request=urllib.request.Request(url,headers={'User-Agent':'CampusCollab/0.2 academic research'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),CheckedRedirect(),PublicHTTPSHandler())
    try:
        with opener.open(request,timeout=30) as response:
            public_url(response.url)
            length=response.headers.get('Content-Length')
            if length and int(length)>max_bytes:
                raise ValueError('Public source exceeds byte limit')
            data=response.read(max_bytes+1)
            if len(data)>max_bytes:
                raise ValueError('Public source exceeds byte limit')
            return data,response.headers,response.url
    except urllib.error.URLError as exc:
        # Windows native curl can build an intermediate certificate chain that
        # the Python OpenSSL store lacks. Keep certificate verification enabled.
        if not isinstance(exc.reason,ssl.SSLError) or not shutil.which('curl'):
            raise
        return curl_get(url,max_bytes)


def curl_get(url,max_bytes):
    from .tools import public_url
    for hop in range(6):
        public_url(url)
        parts=urlsplit(url)
        addresses=socket.getaddrinfo(parts.hostname,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError('Private/local network access is prohibited')
        addresses.sort(key=lambda row:row[0]!=socket.AF_INET)
        address=addresses[0][4][0]
        pinned='['+address+']' if ':' in address else address
        with tempfile.TemporaryDirectory(prefix='collab-public-get-') as temp:
            from pathlib import Path
            header=Path(temp)/'headers.txt'
            argv=[shutil.which('curl'),'--silent','--show-error','--noproxy','*','--proto','=https',
                  '--connect-timeout','10','--max-time','30','--resolve',f'{parts.hostname}:443:{pinned}',
                  '--dump-header',str(header),'--user-agent','CampusCollab/0.2 academic research','--url',url]
            with subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False) as process:
                data=process.stdout.read(max_bytes+1)
                if len(data)>max_bytes:
                    process.kill()
                    process.wait(timeout=5)
                    raise ValueError('Public source exceeds byte limit')
                code=process.wait(timeout=35)
            if code:
                raise RuntimeError('Verified public HTTPS download failed (curl exit '+str(code)+')')
            raw=header.read_bytes()
            if len(raw)>65536:
                raise ValueError('Public response headers exceed limit')
            blocks=raw.strip().replace(b'\r\n',b'\n').split(b'\n\n')
            last=blocks[-1]
            status_line,_,body=last.partition(b'\n')
            status=int(status_line.split()[1])
            headers=http.client.parse_headers(io.BytesIO(body.replace(b'\n',b'\r\n')+b'\r\n\r\n'))
            if status in {301,302,303,307,308}:
                location=headers.get('Location')
                if not location or hop==5:
                    raise ValueError('Invalid/excessive public redirects')
                url=urljoin(url,location)
                continue
            if not 200<=status<300:
                raise ValueError('Public HTTPS status '+str(status))
            return data,headers,url
    raise ValueError('Public redirect limit exceeded')
