"""Independent UI/BFF and private reader factories; no inference or mutations."""
from flask import Flask, request, jsonify, Response, render_template, g
from .contracts import ReviewError, require, PUBLIC_ERRORS

ERROR_COPY = {
    'review_sign_in_required': ('尚未取得檢視授權', '請使用獲准的管理帳號登入；App 的裝置登記不能代替後台登入。'),
    'review_permission_denied': ('目前權限不足', '此帳號沒有這項檢視權限。請聯絡管理者核對授權，不需重新上傳資料。'),
    'record_not_in_review_snapshot': ('此筆不在目前覆核快照', '不代表原資料已刪除。請返回收件清單，或請管理者核對快照範圍。'),
    'review_catalogue_refresh_required': ('覆核快照已過期或時間不符', '請管理者更新已核對的快照；本頁不會掃描資料桶或自動重建目錄。'),
    'source_changed_refresh_snapshot': ('資料版本已變更', '已停止顯示舊快照。請管理者更新快照，再核對原紀錄；不需重拍或重送。'),
    'source_not_live_or_unverified': ('資料目前不可檢視', '目前物件狀態與快照不符，已停止顯示。請管理者查核，不會改讀歷史版本。'),
    'consent_changed_refresh_required': ('研究同意狀態已變更', '這次檢視已停止。請返回清單重新核對；不會恢復舊同意批次。'),
    'media_access_blocked': ('此批次已停止媒體檢視', '可能已撤回或有待確認的寫入。請返回清單查核狀態；這不代表全部歷史副本已永久刪除。'),
    'review_audit_unavailable': ('檢視稽核暫時無法保存', '這次未提供資料。請管理者檢查稽核服務；不可略過稽核繼續檢視。'),
    'review_state_unavailable': ('同意狀態暫時無法核對', '這次未提供資料。請稍後重新核對，原始資料不會因此被修改。'),
    'consent_state_missing': ('同意證據尚未核對', '缺少可核對的同意狀態，這次未提供資料。請管理者查核，不會自動建立同意。'),
    'review_source_unavailable': ('來源資料暫時無法讀取', '這次未提供資料。請管理者核對來源連線與權限，不需重新上傳。'),
    'review_data_unverified': ('資料完整性尚未確認', '資料或收據未通過核對，這次未提供資料。請管理者查核原檔與快照。'),
    'image_content_unverified': ('影像內容尚未確認', '影像格式或尺寸未通過核對，這次不顯示預覽。原始檔保留。'),
    'invalid_or_expired_cursor': ('清單頁面已失效', '請返回收件清單重新選擇；不會變更資料或重新上傳。'),
    'revision_not_in_snapshot': ('此版次不在目前快照', '請返回收件清單，重新選擇快照內的版次。未顯示不代表原修訂已刪除。'),
}

def failure_response(code,status,*,human=False):
    # Keep the reader, probes and image requests on their JSON contract. Only
    # explicit browser document navigation receives the human error page.
    if (human and request.method=='GET' and not request.path.endswith('/image') and
            not request.path.startswith('/static/') and
            request.accept_mimetypes['text/html']>request.accept_mimetypes['application/json']):
        safe=code if PUBLIC_ERRORS.get(code)==status else 'review_unavailable'
        default={400:('檢視要求無法核對','請返回收件清單重新選擇，不需重送資料。'),
                 401:('尚未取得檢視授權','請使用獲准的管理帳號登入，或請管理者核對服務權限。'),
                 403:('目前權限不足','請管理者核對這個入口的權限，不需重新上傳資料。'),
                 404:('找不到此檢視頁面','請返回收件清單重新選擇；這不代表原始資料已刪除。'),
                 409:('檢視狀態已變更','已停止這次顯示，請返回清單重新核對。'),
                 410:('此資料目前不可檢視','請返回清單查核同意與資料狀態。'),
                 429:('暫時無法處理更多檢視','請稍後再試，無須重新上傳。')}
        title,message=ERROR_COPY.get(safe,default.get(status,('檢視服務暫時無法使用','請稍後再試，或請管理者查核。原紀錄不會因此被修改。')))
        response=Response(render_template('error.html',title=title,message=message,code=safe),status=status,content_type='text/html; charset=utf-8')
        response.vary.add('Accept')
        return response
    return jsonify(error=code),status

def hardened(app,*,human=False):
    app.config.update(MAX_CONTENT_LENGTH=1024)
    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'private, no-store, max-age=0','Pragma':'no-cache',
            'Content-Security-Policy':"default-src 'none'; img-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            'X-Frame-Options':'DENY','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Permissions-Policy':'camera=(), microphone=(), geolocation=()'})
        return response
    @app.errorhandler(ReviewError)
    def failure(exc):return failure_response(exc.code,exc.status,human=human)
    @app.errorhandler(Exception)
    def unavailable(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc,HTTPException):return failure_response('review_route_unavailable',exc.code,human=human)
        return failure_response('review_unavailable',503,human=human)
    return app

def arguments(allowed):
    require(not request.get_data() and set(request.args)<=set(allowed) and
        all(len(request.args.getlist(k))==1 for k in request.args),'invalid_review_request',400)

def reader_app(reader,verify_iap,verify_service):
    app=hardened(Flask('lite-review-reader',static_folder=None))
    @app.before_request
    def identity():
        verify_service(request.headers.get('Authorization'))
        g.viewer=verify_iap(request.headers.get('X-Lite-Review-IAP'))
    @app.get('/v1/records')
    def records():
        arguments({'source','cursor'})
        return jsonify(reader.page(g.viewer,request.args.get('source','all'),request.args.get('cursor')))
    @app.get('/v1/records/<rid>')
    def detail(rid):arguments(set());return jsonify(reader.detail(g.viewer,rid))
    @app.get('/v1/records/<rid>/image')
    def image(rid):arguments(set());return Response(reader.image(g.viewer,rid),content_type='image/jpeg')
    return app

def portal_app(verify_iap,backend):
    app=hardened(Flask(__name__),human=True)
    @app.template_filter('taipei_time')
    def taipei_time(value):
        from datetime import datetime, timezone, timedelta
        return datetime.fromtimestamp(value,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S（台灣時間）')
    @app.before_request
    def identity():
        g.iap=request.headers.get('X-Goog-IAP-JWT-Assertion')
        g.viewer=verify_iap(g.iap);g.viewer.check('lite.review.metadata')
    @app.get('/')
    def records():
        arguments({'source','cursor'})
        result=backend('/v1/records',dict(request.args),g.iap,False)
        return render_template('review.html',page=result,detail=None,source=request.args.get('source','all'))
    @app.get('/records/<rid>')
    def detail(rid):
        arguments({'revision'})
        from .contracts import hexid
        require(hexid(rid,64),'record_not_found',404)
        result=backend('/v1/records/'+rid,{},g.iap,False)
        revisions=result.get('revisions',[])
        selected=revisions[-1] if revisions else None
        requested=request.args.get('revision')
        if requested is not None:
            require(requested.isascii() and requested.isdecimal() and len(requested)<=7 and
                str(int(requested))==requested,'invalid_revision',400)
            selected=next((r for r in revisions if r['revision']==int(requested)),None)
            require(requested=='0' or selected is not None,'revision_not_in_snapshot',400)
        return render_template('review.html',page=None,detail=result,source='all',selected_revision=selected)
    @app.get('/records/<rid>/image')
    def image(rid):
        arguments(set());g.viewer.check('lite.review.media')
        from .contracts import hexid
        require(hexid(rid,64),'record_not_found',404)
        return Response(backend('/v1/records/'+rid+'/image',{},g.iap,True),content_type='image/jpeg')
    return app
