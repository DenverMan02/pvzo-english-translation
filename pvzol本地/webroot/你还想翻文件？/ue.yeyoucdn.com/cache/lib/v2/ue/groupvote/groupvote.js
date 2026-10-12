/**
 * Name: \u9876\u8e29\u6295\u7968
 * Create: 2013-03-01
 * Version: 1.1
 * 
 * Author: bsky1wcj
 * Mail:changjianwang@cyou-inc.com   10877098@qq.com
 * Tel:13405967686
 * 
 * 
 * Update Log:2013-03-01
 * 1.modify --	
 
 

 */ 
 /************************************/

var GroupVote = function(opt) {
	this.opts = $.extend({},{
		debug : false, //\u5f00\u542fbug\u8c03\u8bd5
		channel: null, //\u9891\u9053id  //\u6d4b\u8bd5\u8fc7\u597d\u50cf\u6ca1\u7528
		kind: 2, //\u7c7b\u522b\u6807\u793a  1\uff1a\u9875\u9762pv\u70b9\u51fb\u6570  2\uff1a\u9876\u3001\u8e29\u6295\u7968\u6570
		pvLabel: '.js-pv-label',//\u9875\u9762pv\u70b9\u51fb\u6570
		groupVoteItem : '.js-groupvote-item', //\u652f\u6301\u6295\u7968\u5bb9\u5668
		supportLabel:  '.js-support-label', //\u652f\u6301\u6570\u91cf
		supportBtn : '.js-support-btn', //\u652f\u6301\u6309\u94ae
		opposeLabel: '.js-oppose-label',//\u53cd\u5bf9\u6570\u91cf
		opposeBtn : '.js-oppose-btn',//\u53cd\u5bf9\u6309\u94ae
		groupVoteAlertCss : 'js-groupvote-alert',//\u63d0\u793a\u6807\u7b7e
		callback: null
	},opt);
	this.keyLists = null;
	this.opts.debug && console.log('opts=',this.opts);
};



//\u83b7\u53d6\u9876\u8e29\u6570
GroupVote.prototype ={
	//\u83b7\u53d6\u9876\u8e29\u6570
	getData: function(options){
		var o=this;
		o.opts.debug && console.log("GroupVote.prototype.getData");
		var opts = $.extend({},{
			param:{
				channel : o.opts.channel ,//\u9891\u9053id  //\u6d4b\u8bd5\u8fc7\u597d\u50cf\u6ca1\u7528
				kind : o.opts.kind, //\u7c7b\u522b\u6807\u793a  1\uff1a\u9875\u9762pv\u70b9\u51fb\u6570  2\uff1a\u9876\u3001\u8e29\u6295\u7968\u6570
				key_list : null,// \u9875\u9762\u53c2\u6570\u503c   CMS\u9891\u9053\u53f7+\u9875\u9762ID+\u7c7b\u522bID,\u591a\u4e2aid\u503c\u4e2d\u95f4\u4ee5\u534a\u89d2\u9017\u53f7\u95f4\u9694
				_random : Math.random()
			},
			callback : function(data){
				o.opts.debug && console.log('data=',data);
				o.decorate(data);
				
			},
			url	: "http://hits.17173.com/port/hit_batch_read.php?jsonp=?"
			
		},options);
		
		if(!opts.param.key_list) return;
		$.ajax({
			type:"GET",
			url: opts.url,
			data:opts.param,
			dataType:'jsonp',
			scriptCharset:'UTF-8',
			success:function( data ){
				if( data && opts.callback ){
					opts.callback(data);
				}
			}
		});
		
	},	
	
	setData : function(options){
		var o=this;
		o.opts.debug && console.log("GroupVote.prototype.setData");
		var opts = $.extend({},{
			param:{
				channel : null ,//\u9891\u9053id
				web_id : null, //\u9875\u9762ID
				kind : 2, //1:\u65b0\u95fb  2:\u6587\u7ae0  3:FLASH  4:\u56fe\u7247   5:\u89c6\u9891   6:\u97f3\u9891   7:\u6e38\u620fIQ\u6295\u7968   8:\u6e38\u620f  9:\u5382\u5546
				action: 1, // 0:\u53ea\u8bfb\u53d6\u6570\u636e\u30011\uff1a\u652f\u6301\u30012\uff1a\u53cd\u5bf9
				_random : Math.random()
			},
			callback : null,
			url	: "http://hits.17173.com/support/support_opb.php?jsonp=?"
			
		},options);
		if(!opts.param.channel || !opts.param.web_id || !opts.param.kind) return;
		$.ajax({
			type:"GET",
			url: opts.url,
			data:opts.param,
			dataType:'jsonp',
			scriptCharset:'UTF-8',
			success:function( data ){
				if( data && opts.callback ){
					opts.callback(data);
				}

			}
		});
		
	},
	//\u6295\u7968\u540e \u753b\u9762\u54cd\u5e94
	warpResponse : function(data,obj){
		var o=this;
		o.opts.debug && console.log(data,obj);	
		if (data.flag == 1){
			obj.find(o.opts.supportLabel).html( parseInt(data.support) );
			obj.find(o.opts.opposeLabel).html( parseInt(data.oppose) );
			o.alert("\u6295\u7968\u6210\u529f\uff0c\u611f\u8c22\u4f60\u7684\u53c2\u4e0e\uff01");
		}
		else if(data.flag == 9){
			o.alert("\u60a8\u7684IP\u5df2\u7ecf\u6295\u8fc7\u7968\u4e86\uff0c\u611f\u8c22\u60a8\u7684\u53c2\u4e0e\uff01");	
		} else {
			o.alert('\u6295\u7968\u5931\u8d25\uff0cflag='+data.flag);
		}
		
		o.opts.callback && o.opts.callback(data,obj);
	},
	//\u5c06\u6570\u636e\u7ed1\u5b9a\u5230\u9875\u9762\u5bf9\u8c61\u4e0a  \u4fee\u9970
	decorate : function(data){
		if(!data)return;
		var o = this;
		o.opts.debug && console.log("GroupVote.prototype.decorate");
		
		for(var dd in data){
			var vote = data[dd];
			
			switch (o.opts.kind){
				case 1 :
					var pv = vote ? vote : 0;
					var $pvLabel = $(o+'[data-grouppv='+dd+']').find(o.opts.pvLabel);
					$pvLabel.html( pv );
					break;
				case 2 :
					var support = parseFloat( vote.split("#")[0] );
					var oppose = parseFloat( vote.split("#")[1] );
		
					var $supportLabel = $(o+'[data-groupvote='+dd+']').find(o.opts.supportLabel),
						$opposeLabel = $(o+'[data-groupvote='+dd+']').find(o.opts.opposeLabel);
					$supportLabel.html( support );
					$opposeLabel.html( oppose );
					break;
				
				default :

			}
			
		}
		
		
		
	},
	// \u652f\u6301
	supportAction : function(params,selfObj){
		var o = this;
		o.setData({
			param:{
				channel : params.channel ,//\u9891\u9053id
				web_id : params.web_id, //\u9875\u9762ID
				kind : params.kind,
				action: 1, // 0:\u53ea\u8bfb\u53d6\u6570\u636e\u30011\uff1a\u652f\u6301\u30012\uff1a\u53cd\u5bf9
				_random : Math.random()
				
			},
			callback : function(data){
				o.warpResponse(data , selfObj);
			}
		});
	},
	//\u53cd\u5bf9
	opposeAction : function(params,selfObj){
		var o = this;
		o.setData({
			param:{
				channel : params.channel ,//\u9891\u9053id
				web_id : params.web_id, //\u9875\u9762ID
				kind : params.kind,
				action: 2, // 0:\u53ea\u8bfb\u53d6\u6570\u636e\u30011\uff1a\u652f\u6301\u30012\uff1a\u53cd\u5bf9
				_random : Math.random()
				
			},
			callback : function(data){
				o.warpResponse(data , selfObj);
			}
		});
	},
	//\u83b7\u53d6\u6d4f\u89c8\u5668\u663e\u793a\u90e8\u5206\u7684\u5bbd\u5ea6\u9ad8\u5ea6
	getWindowSize :	function() {
		var client = {
			x:0,
			y:0
		};
	 
		if(typeof document.compatMode != 'undefined' && document.compatMode == 'CSS1Compat') {
			client.x = document.documentElement.clientWidth;
			client.y = document.documentElement.clientHeight;
		} else if(typeof document.body != 'undefined' && (document.body.scrollLeft || document.body.scrollTop)) {
			client.x = document.body.clientWidth;
			client.y = document.body.clientHeight;
		}
	 
		return client;
	},
		
	alert : function(msg){
		var o = this;
		o.opts.debug && console.log("GroupVote.prototype.alert");
		var windowSize =o.getWindowSize();
		var winWidth = windowSize.x,
			winHeight = windowSize.y;
			scrollTop = $(document).scrollTop();
			o.opts.debug && console.log("winWidth=",winWidth,"winHeight=",winHeight);

			$groupvoteAlert = $('.'+o.opts.groupVoteAlertCss+':eq(0)');
			$groupvoteAlert.html( msg )
				.css({left:(winWidth-$groupvoteAlert.width())/2+'px',top:(winHeight-$groupvoteAlert.height())/2+scrollTop+'px'})
				.show().delay(800).fadeOut(400);
	},
	alertInit : function(){
		var o = this;
		o.opts.debug && console.log("GroupVote.prototype.alertInit");
		var alertTemp = '<div class="'+o.opts.groupVoteAlertCss+'">\u6295\u7968\u6210\u529f, \u611f\u8c22\u60a8\u7684\u53c2\u4e0e\uff01</div>';
		var cssTempArr = [];
		cssTempArr.push('<style type="text/css">');
		cssTempArr.push('.'+o.opts.groupVoteAlertCss+'{ padding:30px; border:#ecdda0 solid 1px; background:#ffffe5; color:#333333; text-align:center; min-width:350px; z-index:99999; position:absolute; font-family:"\5fae\8f6f\96c5\9ed1"; _width:expression(this.scrollwidth < 350 ? "350px" : "auto"); font-size:14px; display:none;}');
		cssTempArr.push('</style>');
		cssTemp = cssTempArr.join('');
		$('body').append(cssTemp);	
		
		$('body').append( alertTemp );
	},
	init : function(){
		var o=this;
		o.opts.debug && console.log("GroupVote.prototype.init");
		o.alertInit();
		var _keyLists=[];
		$( o.opts.groupVoteItem ).each(function(index, element){
			
			var self = $(this);
			if ( self.length <= 0 ) return;
	
			try {
				var channel = self.attr("data-channel");
				var webID = self.attr("data-webid");
				var kind = self.attr("data-kind");
				var keyList = channel + webID + kind;
				
				
				
				switch (o.opts.kind){
					case 1 :
						self.attr( "data-grouppv" , keyList );
						break;
					case 2 :
						self.attr( "data-groupvote" , keyList );
						var $supportBtn = self.find(o.opts.supportBtn),
							$supportLabel = self.find(o.opts.supportLabel),//\u652f\u6301\u6570\u91cf
							$opposeBtn = self.find(o.opts.opposeBtn),
							$opposeLabel = self.find(o.opts.opposeLabel);//\u53cd\u5bf9\u6570\u91cf

						$supportBtn.live('click',function(){
							o.supportAction( {channel : channel , web_id : webID, kind : kind},self );	
						});
						
						$opposeBtn.live('click',function(){
							o.opposeAction( {channel : channel , web_id : webID, kind : kind},self );
						});
						break;
					
					default :

				}
				_keyLists.push(keyList);
				
	
			}
			catch(exception){}
		});
		
		if (!_keyLists && _keyLists.length<=0) return;
		
		o.keyLists = _keyLists.join(",");
		
		o.getData({
			param:{
				channel : o.opts.channel ,
				kind : o.opts.kind, 
				key_list : o.keyLists ,
				_random : Math.random()
			}

			
		});
		
	}

	
	
		
};

//$(function() {		
//	var groupVote = new GroupVote();
//	groupVote.init();
//});	