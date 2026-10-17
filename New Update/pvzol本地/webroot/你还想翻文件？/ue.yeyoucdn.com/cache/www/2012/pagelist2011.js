var uri = location.href.split("/");
var page = uri[uri.length - 1].split("_");
if (page.length > 1) {
	page = page[1];
	page = parseInt(page.split(".")[0]);
}
else {
	page = 0;
}
var page_offset = (page == 1) ? 0 : (page - 1) * page_count;
var prvPage = (page_offset == 0) ? 0 : page - 1;
var nxtPage = (page_offset + page_count >= totalrecord) ? page : page + 1;
var TotalPage = Math.ceil(totalrecord / page_count);
TotalPage = (TotalPage == 0) ? 1 : TotalPage;
var pageHTML = "";
pageBase = "<div class='pager'><ul class='yiiPager'>";
if (TotalPage == 1 || page == 0)
	pageHTML += "<li class='first hidden'><a href='" + theLink + ".shtml'>\u9996\u9875</a></li>";
else
	pageHTML += "<li class='first'><a href='" + theLink + ".shtml'>\u9996\u9875</a></li>";
if (prvPage < 1) {
	tmpLink = theLink;
}
else {
	tmpLink = theLink + "_" + prvPage;
}
if (TotalPage == 1 || page == 0)
	pageHTML += "<li class='previous hidden'><a title='\u4e0a\u4e00\u9875' href='" + tmpLink + ".shtml'>\u4e0a\u4e00\u9875</a></li>";
else
	pageHTML += "<li class='previous'><a title='\u4e0a\u4e00\u9875' href='" + tmpLink + ".shtml'>\u4e0a\u4e00\u9875</a></li>";
if (TotalPage <= 10) {
	var offset_left = 1;
	var offset_right = TotalPage;
}
else {
	if (page - 5 < 1) {
		var offset_left = 1;
		var offset_right = 10;
	}
	else {
		if (page + 4 > TotalPage) {
			var offset_left = TotalPage - 9;
			var offset_right = TotalPage;
		}
		else {
			var offset_left = page - 5;
			var offset_right = page + 4;
		}
	}
}
for (var i = offset_left; i <= offset_right; i++) {
	j = i - 1;
	if (0 == j) {
		tmpLink = theLink;
	}
	else {
		tmpLink = theLink + "_" + j;
	}
	if (page == j)
		pageHTML += "<li class='page selected'><a href='javascript:;' onfocus='this.blur()'>" + i + "</a></li>";
	else
		pageHTML += "<li class='page'><a href='" + tmpLink + ".shtml'>" + i + "</a></li>";
}

tempPage = nxtPage;
if (TotalPage == nxtPage || TotalPage == 1) //nxtPage
	pageHTML += "<li class='next hidden'><a title='\u4e0b\u4e00\u9875' href='" + theLink + "_" + tempPage + ".shtml'>\u4e0b\u4e00\u9875</a></li>";
else
	pageHTML += "<li class='next'><a title='\u4e0b\u4e00\u9875' href='" + theLink + "_" + tempPage + ".shtml'>\u4e0b\u4e00\u9875</a></li>";
tempPage = TotalPage - 1;
if (TotalPage == nxtPage || TotalPage == 1)
	pageHTML += "<li class='last hidden'><a href='" + theLink + "_" + tempPage + ".shtml'>\u672b\u9875</a></li>";
else
	pageHTML += "<li class='last'><a href='" + theLink + "_" + tempPage + ".shtml'>\u672b\u9875</a></li>";
pageHTML += '<li class="goto"><input id="gotopage" class="gotopage" type="text"><a id="gotopage_link" href="javascript://"><span>\u8df3\u8f6c</span></a></li></ul></div>'
if (document.getElementById("topPage")) {
	document.getElementById("topPage").innerHTML = pageHTML;
}
document.write(pageBase + pageHTML);
var goToPage = function (pageVal) {
	if (!pageVal) {
		pageVal = parseInt($('#gotopage').val());
	}
	if (pageVal <= TotalPage && pageVal > 0) {
		pageVal = pageVal - 1;
		var link = theLink + "_" + pageVal + '.shtml';
		if (pageVal == 0) {
			link = theLink + '.shtml';
		}
		location.href = link;
	}
}
$('#gotopage_link').live('click',function(){goToPage()});
$('#gotopage').live('keydown',function(e){
	try{
		if(e.keyCode == 13){
			goToPage();
		}
	}catch(e){};
});
