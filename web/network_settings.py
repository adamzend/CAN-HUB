"""Ethernet settings; the web process only queues validated requests."""
import ipaddress, json, subprocess
from pathlib import Path

def run(*args):
    return subprocess.check_output(args, text=True, timeout=8).strip()

def profile():
    return run('nmcli','-g','GENERAL.CON-UUID','device','show','eth0')

def snapshot():
    uuid=profile()
    def field(name): return run('nmcli','-g',name,'connection','show',uuid)
    address=field('ipv4.addresses').split(',')[0]
    current=run('nmcli','-g','IP4.ADDRESS','device','show','eth0').splitlines()
    ip,prefix=(address.split('/') if '/' in address else ['', '24'])
    return dict(ip=ip,prefix=int(prefix),gateway=field('ipv4.gateway'),dns=field('ipv4.dns'),
                current_ip=current[0].split('/')[0] if current else '',method=field('ipv4.method'),
                restart_required=bool(current and current[0]!=address))

def validate(data):
    if not isinstance(data,dict): raise ValueError('Nieprawidłowa konfiguracja sieci.')
    try:
        ip=ipaddress.IPv4Address(str(data.get('ip','')).strip())
        raw_prefix=data.get('prefix',24)
        if not isinstance(raw_prefix,(int,str)) or not str(raw_prefix).isdigit(): raise ValueError()
        prefix=int(raw_prefix)
        if isinstance(data.get('prefix'),bool) or not 1<=prefix<=30: raise ValueError()
        net=ipaddress.IPv4Network(f'{ip}/{prefix}',strict=False)
        if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local or ip.is_reserved or ip in (net.network_address,net.broadcast_address): raise ValueError()
        gateway=str(data.get('gateway','')).strip()
        if gateway:
            gw=ipaddress.IPv4Address(gateway)
            if gw not in net or gw in (ip,net.network_address,net.broadcast_address): raise ValueError()
        dns=str(data.get('dns','')).replace(' ',',').split(',')
        dns=[str(ipaddress.IPv4Address(x.strip())) for x in dns if x.strip()]
        if len(dns)>4: raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('Podaj poprawny adres IPv4, prefiks 1–30 i bramę z tej samej podsieci. DNS: do 4 adresów IPv4.')
    return dict(ip=str(ip),prefix=prefix,gateway=gateway,dns=','.join(dns))

def queue(data_dir, data):
    value=validate(data)
    path=Path(data_dir)/'network-request'
    if path.exists(): raise ValueError('Poprzednia zmiana sieci jest jeszcze przetwarzana.')
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value));temp.chmod(0o600);temp.replace(path)
    return value

def apply(data_dir, runner=run):
    """Root helper. Change saved profile only; active connection stays untouched."""
    root=Path(data_dir);path=root/'network-request'
    try:
        value=validate(json.loads(path.read_text()))
        uuid=profile()
        runner('nmcli','connection','modify',uuid,'ipv4.method','manual',
               'ipv4.addresses',f"{value['ip']}/{value['prefix']}",
               'ipv4.gateway',value['gateway'],'ipv4.dns',value['dns'])
        result=dict(ok=True,**value,restart_required=True)
    except Exception as exc:
        result=dict(ok=False,error=str(exc))
    out=root/'network-result.json';tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(result));tmp.chmod(0o644);tmp.replace(out)
    path.unlink(missing_ok=True)

if __name__=='__main__': apply('/var/lib/can-hub-web')
