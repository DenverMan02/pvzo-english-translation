/**
*slideshow
**/
(function ($, _ben) {
    //\u63d0\u4f9bjquery\u8c03\u7528\u63a5\u53e3
    $.fn.farmerSlide = function (options) {
        this.each(function (i, n) {
            new _ben.Slide(n, options)
        });
    };
    var isIE = !!window.ActiveXObject;
    var isIE6 = isIE && !window.XMLHttpRequest;
    _ben.UI.extend('Slide', {
        //\u521d\u59cb\u5316\u4e00\u4e9b\u53c2\u6570
        init: function (el, options) {
            this.base(el, options);
            this.opt = $.extend({}, _ben.Slide.defaultOptions, this.opt);
            this.currentIndex = 0;
            this.isAnimating = false;
            //\u9875\u9762\u4e2d\u6ca1\u6709\u6a21\u7248,\u5219\u4f7f\u7528js\u91cc\u914d\u7f6e\u7684\u6a21\u7248
            if (!this.box.findMark('imageContainer')[0]) {
                this.box.append(this.opt.template);
            }
            this.opt.boxWidth = parseFloat(this.box.css('width'));
            this.opt.boxHeight = parseFloat(this.box.css('height'));
            this.initMark();
            this.setup();
        },
        //\u5b89\u88c5\u5404\u90e8\u4ef6
        setup: function () {
            var me = this;
            me.mark.imageContainer.width(me.opt.boxWidth).height(me.opt.boxHeight);
            me.mark.data && me.mark.data.hide();
            me.loadData();
            me.initThumbs();
            me.start();
            if (me.mark.loading && !me.data[0].img) me.mark.loading.hide();
            //\u5148\u52a0\u8f7d\u7b2c\u4e00\u5f20\u56fe\u7247\uff0c\u5b8c\u6bd5\u540e\u518d\u9884\u52a0\u8f7d\u5269\u4e0b\u7684\u6240\u6709\u56fe\u7247
            $(new Image()).load(function () {
                me.data[0].loaded = true;
                me.preDownLoadImage.call(me);
            }).attr('src', me.data[0].img);
        },
        //\u52a0\u8f7d\u5e7b\u706f\u7247\u6570\u636e(\u4e0d\u5305\u62ec\u56fe\u7247\u5185\u5bb9\u672c\u8eab),\u76ee\u524d\u53ea\u6709\u4ecehtml\u6807\u7b7e\u4e2d\u83b7\u53d6,\u5728\u914d\u7f6e\u9879\u91cc\u914d\u7f6e\u4e24\u79cd\u65b9\u5f0f
        loadData: function () {
            var me = this;
            me.data = [];
            if (me.mark.data) {
                me.mark.data.find('li').each(function () {
                    var dataEl = $(this), _data = {}, imgEl = dataEl.findUI('data', 'img'),
                      imgElAniType = imgEl.uiData('slide-animate'), thumbImg = dataEl.findUI('data', 'thumb'),
                        descEl = dataEl.findUI('data', 'desc'), linkEl = dataEl.findUI("data", "link"), titleEl = dataEl.findUI("data", "title");
                    thumbImg = thumbImg || imgEl;
                    _data.img = imgEl.attr('data-src');
                    _data.thumbImg = thumbImg.attr('data-src');
                    _data.desc = descEl.text() || '';
                    _data.title = titleEl.text() || '';
                    _data.link = linkEl.attr('href') || '';
                    me.data.push(_data);
                });
            }
            else if (me.opt.data) {
                me.data = me.opt.data;
            }
            if (me.randomShow) {
                me.data.sort(function (a, b) { return Math.random() - 0.5; });
            }
            me.total = me.data.length;
        },
        preDownLoadImage: function () {
            var me = this, img = new Image(), index = 0;
            $.each(me.data, function (i, n) {
                $(img).load(function () {
                    me.data[i].loaded = true;
                }).attr('src', this.img);
            });
        },
        pushData: function () {
        },
        //\u56fe\u7247\u4e0b\u8f7d\u5b8c\u6bd5\u540e,\u5f00\u59cb\u8fd0\u884c\u5404\u90e8\u4ef6
        start: function () {
            var me = this;
            me.setCurrentImage();
            //me.mark.tool && me.mark.tool.fadeIn(500);
            me.bindAction();
            me.autoToggle();
        },
        _setCurrentImage: function (index, fn) {
            var me = this, _data = me.data[index];
            me.mark.loading && me.mark.loading.hide();
            me.mark.img.attr('src', _data.img);
            me.mark.title && me.mark.title.text(_data.title);
            me.mark.desc && me.mark.desc.text(_data.desc);
            if (me.mark.link) {
                me.mark.link.attr('href', _data.link);
                me.mark.title && me.mark.title.attr('href', _data.link);
                me.mark.desc && me.mark.desc.attr('href', _data.link);
            }
            me.onPageLoaded(index);
            fn && fn();
        },
        //\u6839\u636e\u53c2\u6570\u8bbe\u7f6e\u5f53\u524d\u56fe\u7247,\u6ca1\u6709\u53c2\u6570\u5219\u4f7f\u7528\u5f53\u524d\u914d\u7f6e\u7684\u56fe\u7247
        setCurrentImage: function (index, fn) {
            var me = this;
            index = index === undefined ? me.currentIndex : index;
            me.currentIndex = index;
            var _data = me.data[index];
            if (_data.loaded) {
                me._setCurrentImage(index, fn);
            }
            else {
                me.mark.loading && me.mark.loading.show();
                $(new Image()).load(function () {
                    _data.loaded = true;
                    me._setCurrentImage(index, fn);
                }).attr('src', _data.img);
            }
        },
        autoToggle: function () {
            var me = this;
            if (!me.opt.autoPlay) return;
            me.thumbDirect = 'right';
            me.clearTimer();
            me.timer = setTimeout(function () {
                me.isAuto = true;
                me.nextPage();
            }, me.opt.interval);
        },
        //\u521d\u59cb\u5316\u7f29\u7565\u6761
        initThumbs: function () {
            var me = this;
            var thumbs = '';
            $.each(me.data, function (i, _data) {
                thumbs += me.getThumbItem(i);
            });
            me.mark.thumbs.empty();
            me.mark.thumbs.append(thumbs);
            var thumbItems = me.mark.thumbs.find('.focus-thumb-item');
            me.thumbItemWidth = thumbItems.outerWidth(true);
            //\u53ef\u89c1\u9884\u89c8\u6846\u6570\u91cf
            me.totalVisableThumb = Math.ceil(me.mark.thumbsbox.innerWidth() / me.thumbItemWidth);
            me.noRepeat = me.totalVisableThumb > me.total;
            if (!me.noRepeat) {
                me.mark.thumbsClone = me.mark.thumbs.clone();
                me.mark.thumbs.after(me.mark.thumbsClone);
            }
            //\u521b\u5efa\u9884\u89c8\u76f8\u6846
            me.getThumbGrid();
        },
        getThumbGrid: function () {
            var me = this;
            me.mark.thumbGrid = $('<div class="focus-thumb-grid"></div>');
            me.mark.thumbsbox.append(me.mark.thumbGrid);
        },
        getThumbItem: function (index) {
            var me = this, _data = me.data[index];
            var thumb = '<p class="focus-thumb-item" data-ui-thumb-item><a href="javascript://"><img data-ui-rel="' + index + '" src="' + _data.thumbImg + '"/></a></p>';
            return thumb;
        },
        onThumbAniFinish: function (index) {
            var me = this;
            var currentThumb = me.mark.thumbs.find('.focus-thumb-item:eq(' + index + ')');
          me.mark.thumbs.find('.current').removeClass('current');
            currentThumb.addClass('current');
            if (!me.noRepeat) {
                currentThumb = me.mark.thumbsClone.find('.focus-thumb-item:eq(' + index + ')');
                currentThumb.addClass('current');
            }
            me.autoToggle();
            me.isAnimating = false;
        },
        setCurrentThumb: function (index) {
            this.isAnimating = true;
            var me = this, index = parseInt(index);
            var currentMarginLeft = parseInt(me.mark.thumbs.css('marginLeft')),
              targetMarginLeft = -index * me.thumbItemWidth;
            var leftIndex = Math.abs(currentMarginLeft / me.thumbItemWidth);
            if (!me.isAuto) {
                for (var i = 0; i < me.totalVisableThumb; i++) {
                    var visibleThumbIndex = (leftIndex * 1 + i) % (me.total);
                    if (index === visibleThumbIndex) {
                        var gridLeft = parseInt(me.mark.thumbGrid.css('left'));
                        var targetGridLeft = i * me.thumbItemWidth;
                        if (gridLeft != targetGridLeft) {
                            me.mark.thumbGrid.animate({ 'left': targetGridLeft }, me.opt.thumbAniInterval, '');
                        }
                        me.box.find('.focus-thumb-item').removeClass('current');
                        me.onThumbAniFinish.call(me, index);
                        return
                    }
                }
            }
            if (currentMarginLeft != targetMarginLeft) {
                if (me.thumbDirect == 'right') {
                    //\u5982\u679c\u7b2c\u4e00\u9884\u89c8\u6761\u5df2\u6eda\u52a8\u51fa\u89c6\u91ce,\u5219\u91cd\u7f6e
                    var maxMarginLeft = -me.total * me.thumbItemWidth;
                    if (currentMarginLeft == maxMarginLeft) {
                        me.mark.thumbs.css('margin-left', 0);
                        currentMarginLeft = targetMarginLeft = 0;
                    }
                    targetMarginLeft = currentMarginLeft - me.thumbItemWidth;
                }
                else if (me.thumbDirect == 'left') {
                    if (index === me.total - 1) {
                        currentMarginLeft = -me.total * me.thumbItemWidth
                        me.mark.thumbs.css('margin-left', currentMarginLeft);
                    }
                    targetMarginLeft = currentMarginLeft + me.thumbItemWidth;
                }
                me.box.find('.focus-thumb-item').removeClass('current');
                me.mark.thumbs.animate({ 'margin-left': targetMarginLeft }, me.thumbAniInterval, '', function () {
                    me.onThumbAniFinish.call(me, index);
                });
            }
            else {
                me.onThumbAniFinish(index);
            }
        },
        onPageLoaded: function (index) {
            this.setCurrentThumb(index);
        },
        //\u8df3\u8f6c\u5230\u6307\u5b9a\u9875\u9762,\u4f1a\u6839\u636e\u914d\u7f6e\u64ad\u653e\u52a8\u753b
        goToPage: function (index) {
            var me = this;
            if (index >= 0 && !me.isAnimating && index < me.total) {
                var _data = me.data[me.currentIndex], _aniType = _data.aniType || me.opt.defaultAni;
                if (me.opt.fuckIe6 && isIE6) _aniType = '';
                var oldIndex = me.currentIndex;
                me.setCurrentImage(index, function () {
                    if (me.animateLists[_aniType]) {
                        me.box.find('.ben-slide-clonebox').remove();
                        me.animateLists[_aniType].call(me, oldIndex, index);
                    }
                });
            }
        },
        //\u8df3\u8f6c\u5230\u4e0b\u4e00\u9875
        nextPage: function () {
            var me = this, index = me.currentIndex * 1 + 1;
            if (index >= me.total) index = 0;
            me.goToPage(index);
        },
        //\u8df3\u8f6c\u5230\u4e0a\u4e00\u9875
        prevPage: function () {
            var me = this, index = me.currentIndex - 1;
            if (index < 0) index = me.total - 1;
            me.goToPage(index);
        },
        getBoxClone: function (index) {
          var _data = this.data[index], box_clone;
          if (_data.link) {
            box_clone = $('<div class="ben-slide-clonebox"><a href="' + _data.link + '" target="_blank"><img src="' + _data.img + '" /></a></div>');
          } else {
            box_clone = $('<div class="ben-slide-clonebox"><a href="javascript://"><img src="' + _data.img + '" /></a></div>');
          }
          return box_clone;
        },
        appendBoxClone: function (boxClone) {
            this.mark.imageContainer.append(boxClone);
        },
        onAnimateBegin: function (oldIndex, nextIndex) {
            var me = this;
            me.clearTimer();
            //me.isAnimating = true;
        },
        onAnimateFinish: function (oldIndex, nextIndex) {
            var me = this;
            //me.isAnimating = false;
            //me.timer = setTimeout(function () {
            me.box.find('.ben-slide-clonebox').remove();
            me.autoToggle();
            //}, time);
        },
        clearTimer: function () {
            clearTimeout(this.timer);
            clearTimeout(this.thumbTimer);
        },
        //\u7ed9html\u5143\u7d20\u7ed1\u5b9a\u5e7b\u706f\u7247\u64cd\u4f5c
        bindAction: function () {
            var me = this;
            if (me.mark.next) {
                me.mark.next.click(function () {
                    me.isAuto = false;
                    me.thumbDirect = 'right';
                    me.nextPage();
                });
            }
            if (me.mark.prev) {
                me.mark.prev.click(function () {
                    me.isAuto = false;
                    me.thumbDirect = 'left';
                    me.prevPage();
                });
            }
            if (me.mark.thumbs) {
                me.mark.thumbsbox.click(function (e) {
                    var index = $(e.target).uiData('rel');
                    if (index !== undefined && index !== null) {
                        me.thumbDirect = 'right';
                        me.isAuto = false;
                        me.goToPage(index);
                    }
                });
            }
        },
        //\u52a8\u753b\u5217\u8868,\u8981\u6269\u5c55\u53ef\u7ee7\u627f\u5e76\u91cd\u8f7d\u6b64\u5217\u8868
        animateLists: {
            random: function (oldIndex, nextIndex) {
                var aniArr = ['slideRight','slideLeft', 'lineRight', 'lineTop', 'lineLeft', 'lineBottom', 'fadeIn'];
                var index = Math.floor(Math.random() * 7);
                this.animateLists[aniArr[index]].call(this, oldIndex, nextIndex);
            },
            slideLeft: function (oldIndex, nextIndex) {
                this.animateLists.line.call(this, oldIndex, nextIndex, { direction: 'left', total: 1 });
            },
            slideRight: function (oldIndex, nextIndex) {
                this.animateLists.line.call(this, oldIndex, nextIndex, { direction: 'right', total: 1 });
            },
            lineRight: function (oldIndex, nextIndex) {
                this.animateLists.line.call(this, oldIndex, nextIndex, { direction: 'right', total: 1 });
            },
            lineTop: function (oldIndex, nextIndex) {
                this.animateLists.line.call(this, oldIndex, nextIndex, { direction: 'top', total: 10 });
            },
            lineLeft: function (oldIndex, nextIndex) {
                this.animateLists.line.call(this, oldIndex, nextIndex, { direction: 'left', total: 10 });
            },
            lineBottom: function (oldIndex, nextIndex) {
                this.animateLists.line.call(this, oldIndex, nextIndex, { direction: 'bottom', total: 10 });
            },
            line: function (oldIndex, nextIndex, options) {
                var me = this, width = me.opt.boxWidth, height = me.opt.boxHeight,
                currentImg = me.data[oldIndex].img,
                nextImg = me.data[nextIndex].img;
                me.onAnimateBegin(oldIndex, nextIndex);
                var options = $.extend({}, { direction: 'top', delayType: 'quence', total: 5 }, options || {});
                var easing = this.opt.easing ? this.opt.easing : '';
                var time = 1200 / me.opt.animateSpeed;
                var total = options.total;
                for (i = 0; i < total; i++) {
                    switch (options.direction) {
                        case 'right':

                            var cloneWidth = width;
                            var cloneHeight = Math.ceil(height / total);

                            var _itopc = (cloneHeight * i);
                            var _ileftc = 0;
                            var _ftopc = _itopc;
                            var _fleftc = cloneWidth;

                            var _itopn = _itopc;
                            var _ileftn = -_fleftc;
                            var _ftopn = _itopc;
                            var _fleftn = 0;

                            var _vtop_image = -_itopc;
                            var _vleft_image = 0;
                            break;
                        case 'top':
                            var cloneWidth = Math.ceil(this.opt.boxWidth / total);
                            var cloneHeight = this.opt.boxHeight;

                            var _itopc = 0;
                            var _ileftc = (cloneWidth * i);
                            var _ftopc = -cloneHeight;
                            var _fleftc = _ileftc;

                            var _itopn = cloneHeight;
                            var _ileftn = _ileftc;
                            var _ftopn = 0;
                            var _fleftn = _ileftc;

                            var _vtop_image = 0;
                            var _vleft_image = -_ileftc;
                            break;
                        case 'left':
                            var cloneWidth = this.opt.boxWidth;
                            var cloneHeight = Math.ceil(this.opt.boxHeight / total);

                            var _itopc = (cloneHeight * i);
                            var _ileftc = 0;
                            var _ftopc = _itopc;
                            var _fleftc = -cloneWidth;

                            var _itopn = _itopc;
                            var _ileftn = -_fleftc;
                            var _ftopn = _itopc;
                            var _fleftn = 0;

                            var _vtop_image = -_itopc;
                            var _vleft_image = 0;
                            break;
                        case 'bottom':
                            var cloneWidth = Math.ceil(this.opt.boxWidth / total);
                            var cloneHeight = this.opt.boxHeight;

                            var _itopc = 0;
                            var _ileftc = (cloneWidth * i);
                            var _ftopc = cloneHeight;
                            var _fleftc = _ileftc;

                            var _itopn = -cloneHeight;
                            var _ileftn = _ileftc;
                            var _ftopn = 0;
                            var _fleftn = _ileftc;

                            var _vtop_image = 0;
                            var _vleft_image = -_ileftc;
                            break;
                    }

                    switch (options.delayType) {
                        case 'zebra': default: var delay_time = (i % 2 == 0) ? 0 : 150; break;
                        case 'random': var delay_time = 30 * (Math.random() * 30); break;
                        case 'quence': var delay_time = i * 100; break;
                    }

                    var boxClone = me.getBoxClone(oldIndex);
                    boxClone.find('img').css({ left: _vleft_image, top: _vtop_image });
                    boxClone.css({ top: _itopc, left: _ileftc, width: cloneWidth, height: cloneHeight });
                    me.appendBoxClone(boxClone);
                    boxClone.show();
                    boxClone.delay(delay_time).animate({ top: _ftopc, left: _fleftc }, time, easing);

                    var boxNextClone = me.getBoxClone(nextIndex);
                    boxNextClone.find('img').css({ left: _vleft_image, top: _vtop_image });
                    boxNextClone.css({ top: _itopn, left: _ileftn, width: cloneWidth, height: cloneHeight });
                    me.appendBoxClone(boxNextClone);
                    boxNextClone.show();
                    var callback = (i == (total - 1)) ? function () {
                        me.onAnimateFinish(oldIndex, nextIndex);
                    } : '';
                    boxNextClone.delay(delay_time).animate({ top: _ftopn, left: _fleftn }, time, easing, callback);
                }
            },
            fadeIn: function (oldIndex, nextIndex) {
                var me = this, width = me.opt.boxWidth, height = me.opt.boxHeight,
                  currentImg = me.data[oldIndex].img,
                  nextImg = me.data[nextIndex].img;
                me.onAnimateBegin(oldIndex, nextIndex);
                var boxClone = me.getBoxClone(oldIndex);
                boxClone.css({ top: 0, left: 0, width: width, height: height });
                me.appendBoxClone(boxClone);
                boxClone.show();
                boxClone.fadeOut(1000, function () {
                    me.onAnimateFinish(oldIndex, nextIndex);
                });
            }
        }
    });

    //\u9ed8\u8ba4\u914d\u7f6e
    _ben.Slide.defaultOptions = {
        //\u6570\u636e
        data: null,
        //\u4e3atrue\u65f6\uff0c\u5728ie6\u4e0b\u7981\u7528\u52a8\u753b\u6548\u679c
        fuckIe6: false,
        //\u5bf9\u6570\u636e\u8fdb\u884c\u968f\u673a\u6392\u5e8f
        randomShow: false,
        //\u662f\u5426\u81ea\u52a8\u64ad\u653e
        autoPlay: false,
        //\u5927\u56fe\u5207\u6362\u52a8\u753b\u901f\u5ea6\uff0c\u8d8a\u9ad8\u901f\u5ea6\u8d8a\u5feb
        animateSpeed: 1.5,
        //\u9ed8\u8ba4\u52a8\u753b\u6837\u5f0f\uff0c\u52a8\u753b\u7c7b\u578b\u5217\u8868\uff08fadeIn\uff0cslideRight\uff0clineRight\uff0clineTop\uff0clineLeft\uff0clineBottom\uff0crandom)
        defaultAni: 'fadeIn',
        //\u7f29\u7565\u56fe\u52a8\u753b\u65f6\u95f4\uff08\u6beb\u79d2\uff09
        thumbAniInterval: 200,
        //\u81ea\u52a8\u64ad\u653e\u95f4\u9694\uff08\u6beb\u79d2\uff09
        interval: 3200
    };
})(jQuery, ue);
