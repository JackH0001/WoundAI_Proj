"""Independent UI/BFF and private reader factories; no inference or mutations."""
from flask import Flask, request, jsonify, Response, render_template, g
from .contracts import ReviewError, require

def hardened(app):
    app.config.update(MAX_CONTENT_LENGTH=1024)
    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'private, no-store, max-age=0','Pragma':'no-cache',
            'Content-Security-Policy':"default-src 'none'; img-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            'X-Frame-Options':'DENY','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Permissions-Policy':'camera=(), microphone=(), geolocation=()'})
        return response
    @app.errorhandler(ReviewError)
    def failure(exc):return jsonify(error=exc.code),exc.status
    @app.errorhandler(Exception)
    def unavailable(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc,HTTPException):return jsonify(error='review_route_unavailable'),exc.code
        return jsonify(error='review_unavailable'),503
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
    app=hardened(Flask(__name__))
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
