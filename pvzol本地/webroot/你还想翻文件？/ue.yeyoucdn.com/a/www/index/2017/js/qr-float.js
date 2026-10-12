pandora.use(['$', 'floating'], function($, Floating) {
    var cssStr = '<style type="text/css">\
        .gb-yeyou-ewmfix{ border: 1px solid #dddddd; width:114px ; background:#fff; z-index: 650;}\
        .gb-yeyou-ewmfix-hd{ height:18px; overflow: hidden; font-size: 0}\
        .gb-yeyou-wemfix-close{float: right; display: block;width:17px; height: 17px; border-left: 1px solid #dddddd; border-bottom: 1px solid #dddddd; color: #9e9e9e; font-size: 15px; line-height: 17px; text-align:center;cursor: pointer;}\
        .gb-yeyou-ewmfix-bd .ewmimg{ display:block; padding:3px 14px }\
        .gb-yeyou-ewmfix-bd p{ padding: 0 5px; color: #666; line-height:18px; font-size: 12px; text-align:center; margin-bottom: 5px}\
        </style>';
    $('head').append(cssStr);
    var html = '<div class="gb-yeyou-ewmfix">\
                    <div class="gb-yeyou-ewmfix-hd"><span class="gb-yeyou-wemfix-close">×</span> </div>\
                    <div class="gb-yeyou-ewmfix-bd">\
                      <a href="https://app.17173.com/#utm_source=yeyou" target="_blank" class="ewmimg"><img src="//ue.yeyoucdn.com/a/www/index/2014/images/yeyou-ewm.png" alt="" width="87px" height="87px" /></a>\
                      <p>17173\u624b\u673a\u5ba2\u6237\u7aef\u9875\u6e38\u8d44\u8baf\u5c3d\u5728\u638c\u63e1</p>\
                    </div>\
                  </div>';
    $('body').append(html);

    $('.gb-yeyou-ewmfix .gb-yeyou-wemfix-close').on('click', function() {
        $('.gb-yeyou-ewmfix').remove();
    });

    var positionSettings = {
        'www.yeyou.com': {
            top: 160,
            contentWidth: 1100
        },
        'hao.yeyou.com': {
            top: 386,
            contentWidth: $('.content').length > 0?  $('.content').outerWidth() > 0: 960
        }
    };
    var top = 200, contentWidth = 1100;
    for (var key in positionSettings) {
        if (window.location.hostname.indexOf(key) > -1) {
            top = positionSettings[key].top;
            contentWidth = positionSettings[key].contentWidth;
        }
    }
    new Floating({
        contentWidth: contentWidth,
        element: '.gb-yeyou-ewmfix',
        vertical: 'top',
        horizontal: 'right',
        useAnchor: true,
        offset: {
            x: 8,
            y: top
        }
    });
});