(function () {
  if ($('.top-background-adv')[0]) return;
  var bv = navigator.appVersion;
  var IE6, IE7, IE8, IE9;
  IE9 = bv.search(/MSIE 9/i) != -1;
  IE8 = bv.search(/MSIE 8/i) != -1;
  IE7 = bv.search(/MSIE 7/i) != -1;
  IE6 = bv.search(/MSIE 6/i) != -1;
  if(IE9 || IE8 || IE7){
    IE6 = false;
  }
  var qr = {
    top:290,
    policy:[
      {
        baseURL:'www.yeyou.com',
        init:function(){
          qr.top = 187;
        }
      },
      {
        baseURL:'hao.yeyou.com',
        init:function(){
          qr.top = 386;
        }
      }
    ],
    init: function () {
      var hostname = window.location.hostname;
      if(hostname.indexOf('act.yeyou.com') < 0){
        var policy = this.policy;
        for (var i = 0; i < policy.length; i++) {
          if(hostname.indexOf(policy[i].baseURL) > -1){
              policy[i].init();
              break;
          }
        }
        this.play();
      }
    },
    play:function() {
      this.importCss();
      this.render();
      $(document.body).append(this.wrap);
      this.bindAction();
    },
    bindAction: function () {
      var me = this;
      this.wrap.find('.gb-yeyou-wemfix-close').click(function() {
        me.wrap.remove();
        qr = null;
      });
      me.setPosition(me.top);
      $(window).resize(function(){
        me.setPosition(me.top);
      });
      if(IE6){
        $(window).bind('scroll', function(){
          me.scrollFunc();
        });
      }
    },
    render: function() {
      var html = '<div class="gb-yeyou-ewmfix">'+
                    '<div class="gb-yeyou-ewmfix-hd"><span class="gb-yeyou-wemfix-close">×</span> </div>'+
                    '<div class="gb-yeyou-ewmfix-bd">'+
                      '<a href="http://a.17173.com/tg/channel/index.html?id=A0011502111" target="_blank" class="ewmimg"><img src="http://ue.yeyoucdn.com/a/www/index/2014/images/yeyou-ewm.png" alt="" width="87px" height="87px" /></a>'+
                      '<p>17173\u624b\u673a\u5ba2\u6237\u7aef\u9875\u6e38\u8d44\u8baf\u5c3d\u5728\u638c\u63e1</p>'+
                    '</div>'+
                  '</div>';
      this.wrap = $(html);
    },
    importCss:function() {
      var html = '<style type="text/css">'+
          '.gb-yeyou-ewmfix{ border: 1px solid #dddddd; width:114px ; background:#fff; position:fixed; '+(IE6?'_position: absolute;':'')+' _bottom: "auto"; z-index: 650;}'+
          '.gb-yeyou-ewmfix-hd{ height:18px; overflow: hidden; font-size: 0}'+
          '.gb-yeyou-wemfix-close{float: right; display: block;width:17px; height: 17px; border-left: 1px solid #dddddd; border-bottom: 1px solid #dddddd; color: #9e9e9e; font-size: 15px; line-height: 17px; text-align:center;cursor: pointer;}'+
          '.gb-yeyou-ewmfix-bd .ewmimg{ display:block; padding:3px 14px }'+
          '.gb-yeyou-ewmfix-bd p{ padding: 0 5px; color: #666; line-height:18px; font-size: 12px; text-align:center; margin-bottom: 5px}'+
          '</style>';
      $('head').append(html);
    },
    setPosition:function(){
        var windowWidth= document.documentElement.clientWidth||document.body.clientWidth||0,
        wrapWidth = $('.content').outerWidth()?$('.content').outerWidth():960,
        borderBlank = (windowWidth - wrapWidth)/2 < 0 ? 0:(windowWidth - wrapWidth)/2,
        left = wrapWidth + borderBlank + 4;
        $('.gb-yeyou-ewmfix').attr('style', 'top:'+this.top+'px;left:' + left + 'px;');
    },
    scrollFunc:function(){
        var st = $(document).scrollTop();
        $('.gb-yeyou-ewmfix').css('top', st + this.top);
    }
  };
  qr.init();
})(window);