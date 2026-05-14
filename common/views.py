from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import SignUpForm
from .models import CrawlData
from apps.users.services import UserService


def index(request):
    return render(request, 'core/index.html')


def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('login')
    else:
        form = SignUpForm()
    return render(request, 'core/signup.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        identifier = request.POST.get('username')
        password = request.POST.get('password')
        user = UserService().authenticate_by_identifier(request, identifier, password)
        if user is not None:
            login(request, user)
            return redirect('index')
        return render(request, 'core/login.html', {'error': '로그인 정보를 확인해 주세요.'})
    return render(request, 'core/login.html')


def logout_view(request):
    logout(request)
    return redirect('index')


@login_required
def crawl_data_list(request):
    items = CrawlData.objects.all()
    return render(request, 'core/data_list.html', {'items': items})


@login_required
def crawl_data_detail(request, pk):
    item = get_object_or_404(CrawlData, pk=pk)
    return render(request, 'core/data_detail.html', {'item': item})


def api_crawl_data(request):
    items = CrawlData.objects.all().values('id', 'title', 'source_url', 'created_at')
    return JsonResponse({'crawl_data': list(items)})


def api_user_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({'authenticated': False})
    return JsonResponse({
        'authenticated': True,
        'username': request.user.username,
        'email': request.user.email,
    })
