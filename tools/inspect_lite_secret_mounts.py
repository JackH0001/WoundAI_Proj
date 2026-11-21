"""Inspect selected mount paths without opening their contents or listing unrelated files."""
import argparse
import json
import os
import stat
from pathlib import Path

def inspect(root, role, leaf):
    if role not in {"identity","oauth","api","root-1","root-2","root-3","root-4"} or leaf != Path(leaf).name or not leaf or leaf in (".",".."):
        raise ValueError("unexpected mount or filename")
    parent=root/role
    report={"role":role}
    try:
        directory=parent.lstat()
        report.update(directory_kind=stat.filemode(directory.st_mode)[0],directory_uid=directory.st_uid)
        if not stat.S_ISDIR(directory.st_mode):
            return report
        p=parent/leaf
        meta=p.lstat()
        report.update(file_kind=stat.filemode(meta.st_mode)[0],file_uid=meta.st_uid,
                      file_gid=meta.st_gid,file_mode=oct(stat.S_IMODE(meta.st_mode)))
        resolved=p.resolve(strict=True)
        contained=resolved.is_relative_to(parent.resolve(strict=True))
        report["contained"]=contained
        if contained:
            target=resolved.stat()
            report.update(target_regular=stat.S_ISREG(target.st_mode),target_mode=oct(stat.S_IMODE(target.st_mode)),target_uid=target.st_uid,
                          process_readable=os.access(p,os.R_OK),mount_read_only=bool(os.statvfs(parent).f_flag & os.ST_RDONLY))
    except (OSError, RuntimeError) as exc:
        report["error_type"]=type(exc).__name__
    return report

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("references",nargs="+",help="role/filename from approved config")
    args=parser.parse_args()
    rows=[inspect(args.root,*r.split("/",1)) for r in args.references]
    print(json.dumps({"effective_uid":os.geteuid(),"entries":rows}))
